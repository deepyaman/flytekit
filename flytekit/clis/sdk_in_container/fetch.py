import rich_click as click
from fsspec.callbacks import TqdmCallback
from rich import print
from rich.panel import Panel
from rich.pretty import Pretty

from flytekit import Literal, FlyteContext
from flytekit.clis.sdk_in_container.helpers import get_and_save_remote_with_click_context
from flytekit.core.type_engine import LiteralsResolver
from flytekit.interaction.string_literals import literal_map_string_repr, literal_string_repr
from flytekit.remote import FlyteRemote


@click.command("fetch")
@click.option("--download", required=False, type=str, default=None,
              help="Download the data to the provided location, if it is a remote path, else ignored.")
@click.argument("flyte_data_uri", type=str, required=True, metavar="FLYTE-DATA-URI (of the form flyte://...)")
@click.pass_context
def fetch(ctx: click.Context, flyte_data_uri: str, download: str):
    """
    Retrieve Inputs/Outputs for a Flyte Execution or any of the inner node executions from the remote server.

    The URI can be retrieved from the Flyte Console, or by invoking the get_data API.
    """

    remote: FlyteRemote = get_and_save_remote_with_click_context(ctx, project="flytesnacks", domain="development")
    click.secho(f"Fetching data from {flyte_data_uri}...", dim=True)
    data = remote.get(flyte_data_uri)
    if isinstance(data, Literal):
        p = literal_string_repr(data)
    elif isinstance(data, LiteralsResolver):
        p = literal_map_string_repr(data.literals)
    else:
        p = data
    pretty = Pretty(p)
    panel = Panel(pretty)
    print(panel)
    if download and isinstance(data, Literal):
        uri = None
        if data.scalar.blob:
            uri = data.scalar.blob.uri
        elif data.scalar.structured_dataset:
            uri = data.scalar.structured_dataset.uri
        if uri:
            FlyteContext.current_context().file_access.get_data(data.scalar.blob.uri, download, is_multipart=False,
                                                                callback=TqdmCallback())
