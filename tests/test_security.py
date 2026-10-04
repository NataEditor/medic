import unittest

from app.core.exceptions import AuthenticationError
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)


class TestSecurity(unittest.TestCase):
    def test_password_hashing_and_verification(self) -> None:
        raw = "SecureP@ssw0rd123"
        hashed = hash_password(raw)

        self.assertNotEqual(raw, hashed)
        self.assertTrue(verify_password(raw, hashed))
        self.assertFalse(verify_password("wrong-password", hashed))

    def test_access_token_creation_and_decoding(self) -> None:
        user_id = 42
        token = create_access_token(user_id)
        payload = decode_token(token, expected_type="access")

        self.assertEqual(payload["sub"], str(user_id))
        self.assertEqual(payload["type"], "access")

    def test_refresh_token_type_mismatch(self) -> None:
        user_id = 42
        token = create_refresh_token(user_id)

        # Ожидаем access, но передаём refresh — должна быть ошибка типа токена
        with self.assertRaises(AuthenticationError) as ctx:
            decode_token(token, expected_type="access")
        self.assertEqual(ctx.exception.code, "token_type_mismatch")

    def test_invalid_token(self) -> None:
        with self.assertRaises(AuthenticationError) as ctx:
            decode_token("garbage.token.string", expected_type="access")
        self.assertEqual(ctx.exception.code, "token_invalid")


if __name__ == "__main__":
    unittest.main()
