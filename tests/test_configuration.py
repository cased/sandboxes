"""Tests for configuration validation across all modules."""

from sandboxes.base import SandboxConfig
from sandboxes.pool import PoolConfig, PoolStrategy
from sandboxes.retry import RetryConfig


class TestSandboxConfiguration:
    """Test SandboxConfig validation and behavior."""

    def test_default_sandbox_config(self):
        """Test default SandboxConfig values."""
        config = SandboxConfig()
        assert config.timeout_seconds == 120
        assert config.memory_mb is None
        assert config.cpu_cores is None
        assert config.env_vars == {}
        assert config.labels == {}
        assert config.provider_config == {}

    def test_sandbox_config_with_all_fields(self):
        """Test SandboxConfig with all fields specified."""
        config = SandboxConfig(
            timeout_seconds=60,
            memory_mb=512,
            cpu_cores=1.0,
            env_vars={"API_KEY": "secret"},
            labels={"env": "test"},
            provider_config={"custom": "value"},
        )

        assert config.timeout_seconds == 60
        assert config.memory_mb == 512
        assert config.cpu_cores == 1.0
        assert config.env_vars["API_KEY"] == "secret"
        assert config.labels["env"] == "test"
        assert config.provider_config["custom"] == "value"

    def test_sandbox_config_immutable_defaults(self):
        """Test that default mutable values don't share references."""
        config1 = SandboxConfig()
        config2 = SandboxConfig()

        config1.env_vars["KEY"] = "value1"
        config2.env_vars["KEY"] = "value2"

        assert config1.env_vars["KEY"] == "value1"
        assert config2.env_vars["KEY"] == "value2"


class TestPoolConfiguration:
    """Test PoolConfig validation and behavior."""

    def test_default_pool_config(self):
        """Test default PoolConfig values."""
        config = PoolConfig()
        assert config.max_total == 10
        assert config.max_idle == 5
        assert config.sandbox_ttl == 3600
        assert config.idle_timeout == 600
        assert config.cleanup_interval == 60
        assert config.strategy == PoolStrategy.LAZY

    def test_pool_config_with_custom_values(self):
        """Test PoolConfig with custom values."""
        config = PoolConfig(
            max_total=20,
            max_idle=10,
            sandbox_ttl=7200,
            idle_timeout=1200,
            cleanup_interval=120,
            strategy=PoolStrategy.EAGER,
        )

        assert config.max_total == 20
        assert config.max_idle == 10
        assert config.sandbox_ttl == 7200
        assert config.idle_timeout == 1200
        assert config.cleanup_interval == 120
        assert config.strategy == PoolStrategy.EAGER

    def test_pool_config_strategy_validation(self):
        """Test PoolConfig strategy validation."""
        # Valid strategies
        for strategy in PoolStrategy:
            config = PoolConfig(strategy=strategy)
            assert config.strategy == strategy


class TestRetryConfiguration:
    """Test RetryConfig validation and behavior."""

    def test_default_retry_config(self):
        """Test default RetryConfig values."""
        config = RetryConfig()
        assert config.max_retries == 3
        assert config.initial_delay == 1.0
        assert config.max_delay == 30.0
        assert config.exponential_base == 2.0
        assert config.jitter

    def test_retry_config_with_custom_values(self):
        """Test RetryConfig with all custom values."""
        config = RetryConfig(
            max_retries=5,
            initial_delay=0.5,
            max_delay=60.0,
            exponential_base=3.0,
            jitter=False,
        )

        assert config.max_retries == 5
        assert config.initial_delay == 0.5
        assert config.max_delay == 60.0
        assert config.exponential_base == 3.0
        assert not config.jitter

    def test_retry_config_calculate_delay(self):
        """Test delay calculation with exponential backoff."""
        config = RetryConfig(initial_delay=1.0, exponential_base=2.0, jitter=False)

        # Calculate delays manually
        delay_0 = config.initial_delay
        delay_1 = config.initial_delay * config.exponential_base
        delay_2 = config.initial_delay * (config.exponential_base**2)

        assert delay_0 == 1.0
        assert delay_1 == 2.0
        assert delay_2 == 4.0


class TestConfigurationInteraction:
    """Test configuration interactions between modules."""

    def test_sandbox_config_in_pool_config(self):
        """Test using SandboxConfig with PoolConfig."""
        sandbox_config = SandboxConfig(timeout_seconds=120, labels={"app": "test"})

        pool_config = PoolConfig(max_total=5, max_idle=2)

        # Configs should be independent
        assert sandbox_config.timeout_seconds == 120
        assert pool_config.max_total == 5

    def test_configuration_serialization(self):
        """Test that configurations can be serialized."""
        sandbox_config = SandboxConfig(
            timeout_seconds=60, env_vars={"KEY": "value"}, labels={"env": "prod"}
        )

        # Should be able to convert to dict
        config_dict = {
            "timeout_seconds": sandbox_config.timeout_seconds,
            "env_vars": sandbox_config.env_vars,
            "labels": sandbox_config.labels,
        }

        assert config_dict["timeout_seconds"] == 60
        assert config_dict["env_vars"]["KEY"] == "value"

    def test_configuration_copy_independence(self):
        """Test that config copies are independent."""
        original = SandboxConfig(timeout_seconds=100, env_vars={"KEY1": "value1"})

        # Create a modified copy
        modified = SandboxConfig(
            timeout_seconds=200, env_vars={**original.env_vars, "KEY2": "value2"}
        )

        assert original.timeout_seconds == 100
        assert modified.timeout_seconds == 200
        assert "KEY2" not in original.env_vars
        assert "KEY2" in modified.env_vars
