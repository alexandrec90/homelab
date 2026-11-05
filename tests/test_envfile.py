"""`.env` handling: secrets are generated once and never disturbed."""

from homelab import envfile

EXAMPLE = "# comment\nDB_PASSWORD=\nAPI_SECRET=\nTOKEN=\nTZ=UTC\n"


def fixed():
    return "generated"


def test_parse_env_reads_assignments_and_strips_quotes():
    text = "# note\n\nA=1\nB=\"two;three\"\nC='x'\nD=\n  E=5  \n"
    assert envfile.parse_env(text) == {"A": "1", "B": "two;three", "C": "x", "D": "", "E": "5"}


def test_parse_env_keeps_equals_signs_in_a_value():
    assert envfile.parse_env("A=b=c\n") == {"A": "b=c"}


def test_new_secret_is_long_and_different_each_time():
    first, second = envfile.new_secret(), envfile.new_secret()
    assert len(first) >= 32
    assert first != second


def test_a_first_merge_generates_secrets_and_leaves_tokens_blank():
    merged = envfile.parse_env(envfile.merge_example(EXAMPLE, "", fixed))
    assert merged == {
        "DB_PASSWORD": "generated",  # pragma: allowlist secret
        "API_SECRET": "generated",  # pragma: allowlist secret
        "TOKEN": "",
        "TZ": "UTC",
    }


def test_a_first_merge_keeps_the_comments():
    assert "# comment" in envfile.merge_example(EXAMPLE, "", fixed)


def test_a_later_merge_never_changes_a_value_already_set():
    existing = (
        "DB_PASSWORD=mine\nAPI_SECRET=too\nTOKEN=tok\nTZ=Asia/Tokyo\n"  # pragma: allowlist secret
    )
    assert envfile.merge_example(EXAMPLE, existing, fixed) == existing


def test_a_later_merge_adds_keys_a_new_stack_brought():
    merged = envfile.parse_env(envfile.merge_example(EXAMPLE, "TZ=Asia/Tokyo\n", fixed))
    assert merged["TZ"] == "Asia/Tokyo"
    assert merged["DB_PASSWORD"] == "generated"  # pragma: allowlist secret
    assert merged["TOKEN"] == ""


def test_a_later_merge_fills_a_secret_left_blank():
    merged = envfile.merge_example("DB_PASSWORD=\n", "DB_PASSWORD=\n", fixed)
    assert envfile.parse_env(merged)["DB_PASSWORD"] == "generated"  # pragma: allowlist secret


def test_each_secret_gets_its_own_value():
    merged = envfile.parse_env(envfile.merge_example("A_PASSWORD=\nB_PASSWORD=\n"))
    assert merged["A_PASSWORD"] != merged["B_PASSWORD"]
