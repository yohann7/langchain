"""Safe Knowledge API failure categories."""


class KnowledgeError(RuntimeError):
    """Base class for failures crossing the Knowledge API boundary."""


class KnowledgeAuthenticationError(KnowledgeError):
    pass


class KnowledgeTimeoutError(KnowledgeError):
    pass


class KnowledgeUnavailableError(KnowledgeError):
    pass


class KnowledgeProtocolError(KnowledgeError):
    pass
