from typer import Typer

from . import fetch, forecast, visualize

app = Typer()

app.command()(fetch.fetch)
app.command()(forecast.forecast)
app.command()(visualize.visualize)
