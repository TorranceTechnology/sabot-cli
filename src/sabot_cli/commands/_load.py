"""Loading a vault's ruleset and schema, the same way for every command that works on a vault.

`lint` reads the ruleset leniently because it reports what is wrong with it, `init` has no vault
yet and `recover` must run when the vault cannot be read, so those three do not come here. Every
other command refuses a vault whose ruleset, schema version or extensions cannot be used, with
the same code whichever command it is.
"""

from dataclasses import dataclass
from pathlib import Path

from sabot_cli.rules import Ruleset, Schema, load_ruleset, load_schema, merge_extensions
from sabot_cli.vault import Vault, load_vault


@dataclass(frozen=True)
class Rules:
    """A vault's ruleset and the schema it is written to.

    `fixed_schema` is the packaged schema of the ruleset's version; `schema` is the same with the
    ruleset's extensions merged in.
    """

    ruleset: Ruleset
    fixed_schema: Schema
    schema: Schema

    def load_notes(self, root: Path) -> Vault:
        """Read every note under `root`, parsing the links the merged schema's edges name."""
        return load_vault(root, self.schema.link_properties)


def load_rules(root: Path) -> Rules:
    """Read and check the ruleset of the vault at `root` and the schema it names.

    Raises `RulesetError`: `not-a-vault` without a ruleset file, `ruleset-invalid` for a ruleset
    that cannot be read or whose extensions cannot be merged, `schema-version-unsupported`
    for a schema version this tool does not read.
    """
    ruleset = load_ruleset(root)
    fixed = load_schema(ruleset.schema_version)
    return Rules(ruleset, fixed, merge_extensions(fixed, ruleset))
