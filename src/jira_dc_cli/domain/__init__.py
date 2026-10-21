"""Domain layer: the concepts and rules of the Jira CLI.

The dependency rule of the hexagon: nothing in this package may import from `application`,
`adapters` or any third-party library. It is plain Python and does no I/O.
"""
