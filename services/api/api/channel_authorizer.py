"""`onSubscribe` direct-Lambda channel authorizer for the AppSync Events API
(docs/07-security.md#channel-authorization): "Parses the channel path for the resource id, reads
the resource's ownerSub from DynamoDB, compares to the JWT sub; rejects otherwise."

Configured as a `direct: true`, `REQUEST_RESPONSE` Lambda integration on both channel namespaces
(`infra/lib/realtime-stack.ts`) — AppSync invokes this synchronously for every subscribe attempt
and expects either `None` (allow) or `{"error": "..."}` (deny), confirmed from AWS's own docs for
direct Lambda integrations
(https://docs.aws.amazon.com/appsync/latest/eventapi/direct-lambda-integrations.html).

> **Verify on first real deploy.** The *response* contract above is documented and certain; the
> exact shape of the *invocation event*'s `identity` object for Cognito User Pool auth is not —
> AWS's own docs show only an unauthenticated `"identity": "None"` example. This is reasoned by
> analogy with AppSync's long-established GraphQL resolver context (`identity.sub` directly, not
> nested under `identity.claims.sub`), and written defensively (checks both shapes, denies by
> default on anything unexpected) so a wrong guess fails closed — no channel becomes
> subscribable by a non-owner even if this needs a follow-up fix. Update this note once a real
> subscribe attempt is observed.
"""

from __future__ import annotations

from typing import Any

from aws_lambda_powertools import Logger
from aws_lambda_powertools.utilities.typing import LambdaContext

from api import deps

logger = Logger(service="channel-authorizer")

_DENIED = {"error": "Not authorized to subscribe to this channel."}


def _subscriber_sub(event: dict[str, Any]) -> str | None:
    identity = event.get("identity")
    if not isinstance(identity, dict):
        return None
    sub = identity.get("sub")
    if isinstance(sub, str) and sub:
        return sub
    claims = identity.get("claims")
    if isinstance(claims, dict):
        claim_sub = claims.get("sub")
        if isinstance(claim_sub, str) and claim_sub:
            return claim_sub
    return None


def lambda_handler(event: dict[str, Any], context: LambdaContext) -> dict[str, Any] | None:
    segments = event.get("info", {}).get("channel", {}).get("segments") or []
    subscriber_sub = _subscriber_sub(event)

    if len(segments) != 2 or subscriber_sub is None:
        logger.warning("subscribe denied: malformed channel or missing identity")
        return _DENIED

    namespace, resource_id = segments
    repo = deps.get_repo()

    owner_sub: str | None
    if namespace == "projects":
        project = repo.get_project(resource_id)
        owner_sub = project.owner_sub if project is not None else None
    elif namespace == "conversations":
        conversation = repo.get_conversation(resource_id)
        owner_sub = conversation.owner_sub if conversation is not None else None
    else:
        owner_sub = None

    if owner_sub is None or owner_sub != subscriber_sub:
        logger.warning("subscribe denied: not the resource owner", namespace=namespace)
        return _DENIED

    return None
