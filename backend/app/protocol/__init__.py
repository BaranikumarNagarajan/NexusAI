"""Generic A2A plumbing shared by the agents, the API and the scripts (no invoice logic).

* ``server`` - builds an A2A Starlette app for an agent (Agent Card, JSON-RPC, body-size limit).
* ``client`` - discovers an agent, sends one JSON task, waits for the final state, reads artifacts.
"""
