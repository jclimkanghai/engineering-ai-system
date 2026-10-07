from .production import build_production_pipeline


def build_default_pipeline():
    return build_production_pipeline("allin")
