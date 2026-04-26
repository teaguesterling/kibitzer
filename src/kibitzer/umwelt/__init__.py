"""Kibitzer's umwelt plugin — vocabulary contributions and policy consumer.

Kibitzer doesn't create its own taxon. It registers properties on existing
entities (state.mode, capability.tool) and consumes resolved policy through
the PolicyEngine.

    from kibitzer.umwelt import register_kibitzer_vocabulary, PolicyConsumer
"""

from kibitzer.umwelt.consumer import PolicyConsumer
from kibitzer.umwelt.vocabulary import register_kibitzer_vocabulary

__all__ = ["register_kibitzer_vocabulary", "PolicyConsumer"]
