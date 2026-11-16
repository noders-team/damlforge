from forge.models import Party, template


@template
class Asset:
    issuer: Party
    amount: int

    signatory: list[Party] = [issuer]
