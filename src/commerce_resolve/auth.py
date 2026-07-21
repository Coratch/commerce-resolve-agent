"""提供账号规范化、密码散列和高熵凭证工具。"""

import hashlib
import re
import secrets

from pwdlib import PasswordHash

USERNAME_PATTERN = re.compile(r"^[a-z0-9._-]{3,32}$")
PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 128


class AuthDomainError(ValueError):
    """表示可映射为有限公开错误码的认证领域失败。"""

    def __init__(self, error_code: str) -> None:
        """保存稳定错误码，不携带密码、邀请码或数据库细节。"""

        super().__init__(error_code)
        self.error_code = error_code


def normalize_username(username: str) -> str:
    """规范化并校验 3–32 位 ASCII 用户名。"""

    normalized = username.strip().lower()
    if USERNAME_PATTERN.fullmatch(normalized) is None:
        raise AuthDomainError("invalid_username")
    return normalized


def validate_password(password: str) -> str:
    """校验密码字符长度，不施加误导性的字符种类规则。"""

    if not PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH:
        raise AuthDomainError("invalid_password")
    return password


def generate_secret_token() -> str:
    """生成至少 32 字节熵、适合 Cookie 或邀请码的 URL-safe Token。"""

    return secrets.token_urlsafe(32)


def hash_secret(token: str) -> str:
    """对高熵 Token 计算不可逆摘要，避免数据库保存可用明文。"""

    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class PasswordService:
    """封装 Argon2 密码散列、验证和抗账号枚举的虚假验证。"""

    def __init__(self) -> None:
        """使用 pwdlib 当前推荐算法并预生成虚假验证 Hash。"""

        self._password_hash = PasswordHash.recommended()
        self._dummy_hash = self._password_hash.hash(generate_secret_token())

    def hash(self, password: str) -> str:
        """校验密码长度后返回 Argon2 Hash。"""

        return self._password_hash.hash(validate_password(password))

    def verify(self, password: str, password_hash: str) -> bool:
        """验证密码并把格式错误安全映射为失败。"""

        try:
            return self._password_hash.verify(password, password_hash)
        except (TypeError, ValueError):
            return False

    def dummy_verify(self, password: str) -> None:
        """账号不存在时执行等价 Argon2 工作，减小响应时序差异。"""

        self._password_hash.verify(password, self._dummy_hash)
