from .base import *

DEBUG = True

ALLOWED_HOSTS = ["*"]

REST_FRAMEWORK["DEFAULT_RENDERER_CLASSES"].append(
    "rest_framework.renderers.BrowsableAPIRenderer"
)
