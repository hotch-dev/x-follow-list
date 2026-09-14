from x_follow_list.security.passwords import PasswordHasher


def test_password_hash_is_salted_and_verifiable() -> None:
    hasher = PasswordHasher()

    first = hasher.hash("correct horse battery staple")
    second = hasher.hash("correct horse battery staple")

    assert first != second
    assert "correct horse battery staple" not in first
    assert hasher.verify("correct horse battery staple", first) is True
    assert hasher.verify("wrong password", first) is False


def test_malformed_password_hash_fails_closed() -> None:
    assert PasswordHasher().verify("password", "not-a-valid-hash") is False
    assert (
        PasswordHasher().verify(
            "password",
            "scrypt$n=1073741824,r=8,p=1$c2FsdA==$ZGlnZXN0",
        )
        is False
    )
