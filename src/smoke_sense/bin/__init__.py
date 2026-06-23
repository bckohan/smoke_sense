from typer import Typer

from .fetch import fetch
from .forecast import forecast
from .visualize import visualize

app = Typer()

app.command()(fetch)
app.command()(forecast)
app.command()(visualize)
