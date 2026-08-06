"""Data providers.

`get_provider()` picks the adapter based on config.DATA_PROVIDER, using an
OPERATOR-level API key from the environment. We hold ONE data account (licensed
for redistribution) and deliver prospects to all customers — customers bring
nothing. The optional `api_key` arg is an override for operator CLI/testing.
"""
import logging

from .. import config

log = logging.getLogger("providers")


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
    # Loud guard: if a REAL provider is configured but its key is missing, don't
    # quietly ship fake data — say so plainly in the logs.
    if provider != "mock":
        log.error("DATA_PROVIDER=%s but no API key is set — falling back to MOCK "
                  "(fake) data. Set the provider's API key to deliver real "
                  "prospects.", provider)
    from .mock import MockProvider
    return MockProvider()
