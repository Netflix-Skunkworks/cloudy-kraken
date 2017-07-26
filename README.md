Usage
=====

```python
grizzly_controller.py start [--region=<region>...]  <attack> <threads> <instances> <ttl> <time>
grizzly_controller.py stop [--region=<region>...]
grizzly_controller.py delete [--region=<region>...]
grizzly_controller.py kill [--region=<region>...]
grizzly_controller.py pushconfig
grizzly_controller.py pushfiles <manifest>
```

Notes
=====
when running pushconfig and pushfiles, you currently want to have the cloudy-kraken files as a subdir of repulsive_grizzly.

Run the commands from repulsive_grizzly.
