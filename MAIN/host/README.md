# Composition host

The host loads installed `autograder.modules` entry points, checks capability dependencies and route uniqueness, mounts module routers, and exposes live/readiness endpoints. It intentionally contains no question, submission, grading or user business logic.
