"""Resolve explicit test-environment selection before touching custody paths."""
from pathlib import Path
import re

DEFAULT_CLUSTER = 'ai-sdlc-saferus'


def validate_selected_cluster(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,35}', value):
        raise ValueError('Invalid explicitly selected AI-SDLC cluster')
    return value


def configure(args, *, external):
    cluster = validate_selected_cluster(args.ai_sdlc_cluster)
    if not external and cluster != DEFAULT_CLUSTER:
        raise ValueError('Explicit AI-SDLC cluster selection requires an environment command')
    if external and args.ai_sdlc_state is None:
        args.ai_sdlc_state = Path.home()/'.local/state/ai-sdlc'/cluster
    if args.state is None:
        name = ('ai-sdlc-production' if cluster == DEFAULT_CLUSTER else cluster+'-production') if external else 'demo'
        args.state = Path.home()/'.local/state/saferus'/name
    return args
