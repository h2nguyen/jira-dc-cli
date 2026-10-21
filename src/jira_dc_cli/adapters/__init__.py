"""Adapters: the code that touches the outside world.

`driven` adapters implement the ports of the application (Jira over HTTP, files, config).
`driving` adapters call the application (the command line). Neither kind imports the other.
"""
