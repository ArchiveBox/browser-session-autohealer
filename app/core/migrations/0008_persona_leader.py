from plain.postgres import migrations


def consolidate_leaders(models, schema_editor):
    Leader = models.get_model('core', 'Leader')
    # These are current-state pointers. Preserve every run and checkpoint.
    leaders = list(Leader.query.join('persona', 'checkpoint'))
    winners = {}
    for leader in leaders:
        rank = (leader.verified, leader.finished_at or leader.checkpoint.created_at, leader.id)
        current = winners.get(leader.persona.id)
        if current is None or rank > current[0]:
            winners[leader.persona.id] = (rank, leader.id)
    keep = [entry[1] for entry in winners.values()]
    Leader.query.exclude(id__in=keep).delete()


class Migration(migrations.Migration):
    dependencies = (('core', '0007_check_type_domain_metadata'),)
    operations = (
        migrations.RunPython(consolidate_leaders),
        migrations.RemoveField(model_name='leader', name='scope'),
    )
