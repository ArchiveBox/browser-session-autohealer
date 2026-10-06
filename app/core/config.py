from plain.packages import PackageConfig, register_config


@register_config
class Config(PackageConfig):
    def ready(self):
        from .network import ensure_database

        ensure_database()
