"""The scope bridge: the one process that drives a microscope's motors, camera and light (services/scope_bridge/AGENTS.md).

As the camera bridge alone opens the camera, this alone touches the microscope: Hooke's brain, its voice and its
researchers ask it over HTTP on this Mac, and it checks every action against the limits the user set, asks for the
user's yes before the light goes on or a large acquisition starts, and keeps a receipt of every action.
"""
