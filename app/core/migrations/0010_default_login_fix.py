from plain.postgres import migrations


def seed(models, schema_editor):
    FixType = models.get_model('core', 'FixType')
    FixType.query.get_or_create(key='restore-login', defaults={
        'name': 'Restore login', 'domain': '*', 'author': 'account-checker',
        'prompt': 'Restore this account’s sign-in at the configured login page using the available credential placeholders. '
                  'If it is already signed in, confirm access without signing out. '
                  'Use browser-harness to inspect each step and save a final screenshot. '
                  'Stop if a CAPTCHA or unavailable verification method requires the researcher. '
                  'Do not change account settings or interact with feed content.',
    })


class Migration(migrations.Migration):
    dependencies = (('core', '0009_fixtype_run_fix_type'),)
    operations = (migrations.RunPython(seed),)
