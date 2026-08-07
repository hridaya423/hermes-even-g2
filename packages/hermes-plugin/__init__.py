# ruff: noqa: N999

if __package__:
    from .hermes_g2_plugin import register
else:
    from hermes_g2_plugin import register

__all__ = ["register"]
