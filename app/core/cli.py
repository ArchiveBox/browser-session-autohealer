import json
from pathlib import Path

import click
from plain.cli import register_cli
from plain.runtime import settings


@register_cli("accounts")
@click.group()
def cli():
    """Persona imports, checkouts, and the access-check worker."""


@cli.command()
def init():
    from app.users.models import User

    from .models import Provider, Site

    if not User.query.filter(email="local@account-checker.test").exists():
        User.query.create(
            email="local@account-checker.test",
            password=(Path(settings.APP_CONFIG_DIR) / "admin-password").read_text(),
            is_admin=True,
        )
    if not Provider.query.filter(kind="local").exists():
        Provider.query.create(name="Local · abx-dl", kind="local", config={})
    for domain in [
        "facebook.com",
        "instagram.com",
        "x.com",
        "tiktok.com",
        "youtube.com",
        "reddit.com",
        "linkedin.com",
        "news.ycombinator.com",
    ]:
        Site.query.get_or_create(domain=domain)
    click.echo(
        "Initialized. Local login: local@account-checker.test; password is in the private config directory."
    )


@cli.command()
def discover():
    from .importers import discover

    click.echo(json.dumps(discover(), indent=2))


@cli.command("import")
@click.option("--name", required=True)
@click.option("--profile", required=True, type=click.Path(exists=True, file_okay=False))
@click.option("--browser", type=click.Choice(["brave", "chrome", "native"]), default="brave")
@click.option("--site", multiple=True, help="Import only this domain and its subdomains; repeat per site")
@click.option("--all-sites", is_flag=True, help="Explicitly include all sites")
def import_profile(name, profile, browser, site, all_sites):
    from . import services
    from .importers import import_browser
    from .models import Persona

    if bool(site) == all_sites:
        raise click.UsageError("Choose --site domains or --all-sites")
    persona = Persona.query.filter(name=name).first() or services.create_persona(
        name,
        {
            "timezone": settings.TIME_ZONE,
            "locale": "en-US",
            "viewport": {"width": 1440, "height": 1000, "deviceScaleFactor": 1},
        },
    )
    cp = import_browser(persona, profile, browser, "CLI import", sites=None if all_sites else site)
    click.echo(
        json.dumps(
            {
                "persona": persona.id,
                "checkpoint": cp.digest,
                "summary": cp.summary,
                "coverage": cp.coverage,
            }
        )
    )


@cli.command()
@click.option("--once", is_flag=True)
@click.option("--queued-only", is_flag=True, help="Process requested sessions without scheduling periodic checks")
@click.option("--run", "run_id", type=int, help="Execute only this queued session, without scheduling other checks")
def worker(once, queued_only, run_id):
    from .worker import claim, execute, loop

    if run_id is None:
        loop(once, schedule=not queued_only)
        return
    run = claim(run_id)
    if run is None:
        raise click.ClickException('Session is not queued')
    result = execute(run)
    click.echo(json.dumps({'run': result.id, 'status': result.status, 'promoted': result.promoted}))


@cli.command("run")
@click.option("--check", "check_id", type=int, required=True)
def run_check(check_id):
    from . import services
    from .models import Check

    check = Check.query.get(id=check_id)
    run = services.checkout(check.account.persona.id, check.provider.id,
        check.account.site.domain, "CLI", check_ids=[check.id])
    click.echo(f"Queued session #{run.id}. Run `plain accounts worker --run {run.id}` to execute it.")


@cli.command()
def agent():
    from .inference import ensure_server

    ensure_server()
    click.echo(
        "OpenCode ready in Browser Session Autohealer: http://127.0.0.1:8421/agents"
    )
