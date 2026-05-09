from sqlalchemy.orm import Session


def transform(session: Session) -> int:
    """Read bronze rows and upsert cleaned data into the listings (silver) table.

    Not yet implemented — scaffolding only.
    """
    raise NotImplementedError("silver transformation is not implemented yet")
