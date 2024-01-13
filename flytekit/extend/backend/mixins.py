import asyncio
import signal
import sys
import time
import typing
from collections import OrderedDict
from functools import partial
from types import FrameType, coroutine

import grpc
from flyteidl.admin.agent_pb2 import (
    RUNNING,
    SUCCEEDED,
    CreateTaskResponse,
    GetTaskResponse,
    State,
)
from flyteidl.core import literals_pb2
from flyteidl.core.tasks_pb2 import TaskTemplate
from rich.progress import Progress

from flytekit import FlyteContext, PythonFunctionTask
from flytekit.configuration import ImageConfig, SerializationSettings
from flytekit.core import utils
from flytekit.core.base_task import PythonTask
from flytekit.core.type_engine import TypeEngine
from flytekit.exceptions.user import FlyteUserException
from flytekit.extend.backend.base_agent import AgentRegistry
from flytekit.extend.backend.utils import _get_grpc_context, is_terminal_state, render_task_template
from flytekit.models.literals import LiteralMap


class AsyncAgentExecutorMixin:
    """
    This mixin class is used to run the agent task locally, and it's only used for local execution.
    Task should inherit from this class if the task can be run in the agent.
    It can handle asynchronous tasks and synchronous tasks.
    Asynchronous tasks are tasks that take a long time to complete, such as running a query.
    Synchronous tasks run quickly and can return their results instantly. Sending a prompt to ChatGPT and getting a response, or retrieving some metadata from a backend system.
    """

    _clean_up_task: coroutine = None
    _agent: AgentBase = None
    _entity: PythonTask = None
    _ctx: FlyteContext = FlyteContext.current_context()
    _grpc_ctx: grpc.ServicerContext = _get_grpc_context()

    def execute(self, **kwargs) -> typing.Any:
        ctx = FlyteContext.current_context()
        ss = ctx.serialization_settings or SerializationSettings(ImageConfig())
        output_prefix = ctx.file_access.get_random_remote_directory()

        from flytekit.tools.translator import get_serializable

        self._entity = typing.cast(PythonTask, self)
        task_template = get_serializable(OrderedDict(), ss, self._entity).template
        self._agent = AgentRegistry.get_agent(task_template.type)

        res = asyncio.run(self._create(task_template, output_prefix, kwargs))

        # If the task is synchronous, the agent will return the output from the resource literals.
        if res.HasField("resource"):
            if res.resource.state != SUCCEEDED:
                raise FlyteUserException(f"Failed to run the task {self._entity.name}")
            return LiteralMap.from_flyte_idl(res.resource.outputs)

        res = asyncio.run(self._get(resource_meta=res.resource_meta))

        if res.resource.state != SUCCEEDED:
            raise FlyteUserException(f"Failed to run the task {self._entity.name}")

        # Read the literals from a remote file, if agent doesn't return the output literals.
        if task_template.interface.outputs and len(res.resource.outputs.literals) == 0:
            local_outputs_file = ctx.file_access.get_random_local_path()
            ctx.file_access.get_data(f"{output_prefix}/output/outputs.pb", local_outputs_file)
            output_proto = utils.load_proto_from_file(literals_pb2.LiteralMap, local_outputs_file)
            return LiteralMap.from_flyte_idl(output_proto)

        return LiteralMap.from_flyte_idl(res.resource.outputs)

    async def _create(
        self, task_template: TaskTemplate, output_prefix: str, inputs: typing.Dict[str, typing.Any] = None
    ) -> CreateTaskResponse:
        ctx = FlyteContext.current_context()

        # Convert python inputs to literals
        literals = inputs or {}
        for k, v in inputs.items():
            literals[k] = TypeEngine.to_literal(ctx, v, type(v), self._entity.interface.inputs[k].type)
        literal_map = LiteralMap(literals)

        if isinstance(self, PythonFunctionTask):
            # Write the inputs to a remote file, so that the remote task can read the inputs from this file.
            path = ctx.file_access.get_random_local_path()
            utils.write_proto_to_file(literal_map.to_flyte_idl(), path)
            ctx.file_access.put_data(path, f"{output_prefix}/inputs.pb")
            task_template = render_task_template(task_template, output_prefix)

        if self._agent.asynchronous:
            res = await self._agent.async_create(self._grpc_ctx, output_prefix, task_template, literal_map)
        else:
            res = self._agent.create(self._grpc_ctx, output_prefix, task_template, literal_map)

        signal.signal(signal.SIGINT, partial(self.signal_handler, res.resource_meta))  # type: ignore
        return res

    async def _get(self, resource_meta: bytes) -> GetTaskResponse:
        state = RUNNING
        grpc_ctx = _get_grpc_context()

        progress = Progress(transient=True)
        task = progress.add_task(f"[cyan]Running Task {self._entity.name}...", total=None)
        with progress:
            while not is_terminal_state(state):
                progress.start_task(task)
                time.sleep(1)
                if self._agent.asynchronous:
                    res = await self._agent.async_get(grpc_ctx, resource_meta)
                    if self._clean_up_task:
                        await self._clean_up_task
                        sys.exit(1)
                else:
                    res = self._agent.get(grpc_ctx, resource_meta)
                state = res.resource.state
            progress.print(f"Task state: {State.Name(state)}, State message: {res.resource.message}")
            if hasattr(res.resource, "log_links"):
                for link in res.resource.log_links:
                    progress.print(f"{link.name}: {link.uri}")
        return res

    def signal_handler(self, resource_meta: bytes, signum: int, frame: FrameType) -> typing.Any:
        if self._agent.asynchronous:
            if self._clean_up_task is None:
                self._clean_up_task = asyncio.create_task(self._agent.async_delete(self._grpc_ctx, resource_meta))
        else:
            self._agent.delete(self._grpc_ctx, resource_meta)
            sys.exit(1)