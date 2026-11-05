"""Shared by the tests that drive a deploy."""


class Recorder:
    """Stands in for the runner: records each command, and fails the ones asked to."""

    def __init__(self, fail_on=()):
        self.commands = []
        self.fail_on = fail_on

    def __call__(self, command):
        self.commands.append(list(command))
        return 1 if any(word in command for word in self.fail_on) else 0


def project(tmp_path, stacks='["web"]', env="SITE_PASSWORD=x\n"):
    """A minimal repository on disk."""
    web = "services: {web: {}}\n# ${SITE_PASSWORD:?x}\n# ${DATA_ROOT}/web:/d\n"
    for name, text in {"web": web, "proxy": "services: {caddy: {}}\n"}.items():
        folder = tmp_path / "stacks" / name
        folder.mkdir(parents=True)
        (folder / "compose.yaml").write_text(text, encoding="utf-8")
        (folder / "stack.toml").write_text("memory_mb = 1\n", encoding="utf-8")
    (tmp_path / "inventory.toml").write_text(
        f'domain = "lan"\n[machines.box]\naddress = "10.0.0.5"\nmemory_mb = 9\nstacks = {stacks}\n',
        encoding="utf-8",
    )
    if env is not None:
        (tmp_path / ".env").write_text(env, encoding="utf-8")
    return tmp_path
