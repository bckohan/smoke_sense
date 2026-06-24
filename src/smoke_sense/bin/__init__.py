from typer import Typer

from . import credentials, fetch, forecast, visualize

app = Typer()

app.command()(fetch.fetch)
app.command()(forecast.forecast)
app.command()(visualize.visualize)
app.add_typer(credentials.app, name="credentials")
