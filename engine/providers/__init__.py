"""Data providers.

`get_provider()` picks the adapter based on config.DATA_PROVIDER, using an
OPERATOR-level API key from the environment. We hold ONE data account (licensed
for redistribution) and deliver prospects to all customers — customers bring
nothing. The optional `api_key` arg is an override for operator CLI/testing.
"""
from .. import config


def get_provider(api_key: str = ""):
    provider = (config.DATA_PROVIDER or "mock").lower()
    if provider == "pdl":
        from .pdl import PdlProvider
        key = api_key or config.PDL_API_KEY
        if key:
            return PdlProvider(key)
    elif provider == "apollo":
        from .apollo import ApolloProvider
        key = api_key or config.APOLLO_API_KEY
        if key:
            return ApolloProvider(key)
    from .mock import MockProvider
    return MockProvider()
