from forge.models import Party, template


@template
class Asset:
    issuer: Party
    amount: str

    signatory: list[Party] = [issuer]
