"""
fabric.molyanov.validators
===========================
Thin wrappers that translate molyanov validator invocations into ruflo plugin
subprocess calls and normalise plugin output back to the molyanov finding schema.

Public API::

    from fabric.molyanov.validators.skeptic_wrapper import run as run_skeptic
    from fabric.molyanov.validators.security_auditor_wrapper import run as run_security
    from fabric.molyanov.validators.post_deploy_qa_wrapper import run as run_postdeploy

Each ``run()`` function accepts a molyanov input dict and returns a molyanov
result dict with a ``findings`` list and a ``delegated_to`` field.
"""
