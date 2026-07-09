from mcdata.modrinth import _parse_version


def test_parse_version_preserves_modrinth_file_integrity_metadata() -> None:
    version = _parse_version(
        "example-project",
        {
            "version_number": "1.2.3",
            "version_type": "release",
            "game_versions": ["26.2"],
            "loaders": [],
            "files": [
                {
                    "filename": "example.zip",
                    "url": "https://cdn.modrinth.com/example.zip",
                    "primary": True,
                    "hashes": {"sha512": "sha512-value", "sha1": "sha1-value"},
                    "size": 123,
                }
            ],
        },
    )

    file = version.primary_file
    assert file.sha512 == "sha512-value"
    assert file.sha1 == "sha1-value"
    assert file.size == 123
