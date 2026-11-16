from typing import Optional

from forge.models import Party, template


@template
class Asset:
    issuer: Party
    amount: int
    note: Optional[str]

    signatory: list[Party] = [issuer]
