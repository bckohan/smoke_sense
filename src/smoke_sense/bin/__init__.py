from typer import Typer

from . import credentials, fetch, forecast, outliers, rank, summary, visualize

app = Typer()

app.command()(fetch.fetch)
app.command()(forecast.forecast)
app.command()(summary.summary)
app.command()(rank.rank)
app.command()(outliers.outliers)
app.add_typer(credentials.app, name="credentials")
app.add_typer(visualize.app, name="visualize")
