from plain.postgres import migrations


def populate_domains(apps, schema_editor):
    from app.core.models import Check, CheckType
    from app.core.services import version_check_type
    # Existing source and execution rows are append-only. Record scoped revisions.
    checks = list(Check.query.join('account__site'))
    for check in checks:
        previous = CheckType.query.filter(check_id=check.id).order_by('-created_at').first()
        if previous and previous.domain == '*':
            version_check_type(check, previous=previous, name=previous.name,
                lang=previous.lang, code=previous.code, domain=check.account.site.domain,
                author='migration:domain metadata')


class Migration(migrations.Migration):
    dependencies = (('core', '0006_checktype_domain'),)
    operations = (migrations.RunPython(populate_domains),)
