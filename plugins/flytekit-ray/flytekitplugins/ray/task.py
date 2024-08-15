import base64
import json
import os
import click
import typing
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional


import yaml
from flytekitplugins.ray.models import (
    HeadGroupSpec,
    RayCluster,
    RayJob,
    WorkerGroupSpec,
)
from google.protobuf.json_format import MessageToDict

from flytekit import ImageSpec
from flytekit.configuration import SerializationSettings
from flytekit.core.context_manager import ExecutionParameters, FlyteContextManager, ExecutionState
from flytekit.core.python_function_task import PythonFunctionTask
from flytekit.extend import TaskPlugins
from flytekit.extend.backend.base_agent import AsyncAgentExecutorMixin


@dataclass
class HeadNodeConfig:
    ray_start_params: typing.Optional[typing.Dict[str, str]] = None


@dataclass
class WorkerNodeConfig:
    group_name: str
    replicas: int
    min_replicas: typing.Optional[int] = None
    max_replicas: typing.Optional[int] = None
    ray_start_params: typing.Optional[typing.Dict[str, str]] = None


@dataclass
class RayJobConfig:
    worker_node_config: typing.List[WorkerNodeConfig]
    head_node_config: typing.Optional[HeadNodeConfig] = None
    enable_autoscaling: bool = False
    runtime_env: typing.Optional[dict] = None
    address: typing.Optional[str] = None
    shutdown_after_job_finishes: bool = False
    ttl_seconds_after_finished: typing.Optional[int] = None


class RayFunctionTask(PythonFunctionTask):
    """
    Actual Plugin that transforms the local python code for execution within Ray job.
    """

    _RAY_TASK_TYPE = "ray"

    def __init__(self, task_config: RayJobConfig, task_function: Callable, **kwargs):
        super().__init__(
            task_config=task_config,
            task_type=self._RAY_TASK_TYPE,
            task_function=task_function,
            **kwargs,
        )
        self._task_config = task_config

    def pre_execute(self, user_params: ExecutionParameters) -> ExecutionParameters:
        import ray
        init_params = {"address": self._task_config.address}

        ctx = FlyteContextManager.current_context()
        if not ctx.execution_state.is_local_execution():
            working_dir = os.getcwd()
            init_params["runtime_env"] = {
                "working_dir": working_dir,
                "excludes": ["script_mode.tar.gz", "fast*.tar.gz"],
            }

        ray.init(**init_params)
        return user_params

    def get_custom(self, settings: SerializationSettings) -> Optional[Dict[str, Any]]:
        cfg = self._task_config

        # Deprecated: runtime_env is removed KubeRay >= 1.1.0. It is replaced by runtime_env_yaml
        runtime_env = base64.b64encode(json.dumps(cfg.runtime_env).encode()).decode() if cfg.runtime_env else None

        runtime_env_yaml = yaml.dump(cfg.runtime_env) if cfg.runtime_env else None

        ray_job = RayJob(
            ray_cluster=RayCluster(
                head_group_spec=(
                    HeadGroupSpec(cfg.head_node_config.ray_start_params) if cfg.head_node_config else None
                ),
                worker_group_spec=[
                    WorkerGroupSpec(
                        c.group_name,
                        c.replicas,
                        c.min_replicas,
                        c.max_replicas,
                        c.ray_start_params,
                    )
                    for c in cfg.worker_node_config
                ],
                enable_autoscaling=(cfg.enable_autoscaling if cfg.enable_autoscaling else False),
            ),
            runtime_env=runtime_env,
            runtime_env_yaml=runtime_env_yaml,
            ttl_seconds_after_finished=cfg.ttl_seconds_after_finished,
            shutdown_after_job_finishes=cfg.shutdown_after_job_finishes,
        )
        return MessageToDict(ray_job.to_flyte_idl())


@dataclass
class AnyscaleConfig:
    compute_config: typing.Optional[str] = None


class AnyscaleFunctionTask(AsyncAgentExecutorMixin, PythonFunctionTask):
    _TASK_TYPE = "anyscale"

    def __init__(
        self,
        task_config: AnyscaleConfig,
        task_function: Callable,
        container_image: Optional[typing.Union[str, ImageSpec]] = None,
        **kwargs,
    ):
        super(AnyscaleFunctionTask, self).__init__(
            task_config=task_config,
            task_type=self._TASK_TYPE,
            task_function=task_function,
            container_image=container_image,
            **kwargs,
        )

    def execute(self, **kwargs) -> Any:
        print("Executing Anyscale Task")
        ctx = FlyteContextManager.current_context()
        if ctx.execution_state and ctx.execution_state.mode == ExecutionState.Mode.TASK_EXECUTION:
            return PythonFunctionTask.execute(self, **kwargs)
        return AsyncAgentExecutorMixin.execute(self, **kwargs)

    def execute(self, **kwargs) -> Any:
        try:
            ctx = FlyteContextManager.current_context()
            if not ctx.file_access.is_remote(ctx.file_access.raw_output_prefix):
                raise ValueError(
                    "To submit a Databricks job locally,"
                    " please set --raw-output-data-prefix to a remote path. e.g. s3://, gcs//, etc."
                )
            if ctx.execution_state and ctx.execution_state.is_local_execution():
                return AsyncAgentExecutorMixin.execute(self, **kwargs)
        except Exception as e:
            click.secho(f"❌ Agent failed to run the task with error: {e}", fg="red")
            click.secho("Falling back to local execution", fg="red")
        return PythonFunctionTask.execute(self, **kwargs)


# Inject the Ray plugin into flytekits dynamic plugin loading system
TaskPlugins.register_pythontask_plugin(RayJobConfig, RayFunctionTask)
TaskPlugins.register_pythontask_plugin(AnyscaleConfig, AnyscaleFunctionTask)
