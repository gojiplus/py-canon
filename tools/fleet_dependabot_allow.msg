Track transitive dependencies in Dependabot

Dependabot's default keeps only the dependencies pyproject.toml names
current. Everything they pull in sits in uv.lock untouched until an
advisory or an upstream floor forces the issue: gojiplus/reporoulette
carried grpcio 1.76 for eleven months, then google-auth 2.57 started
warning at import that 1.83 is the floor, and with warnings as errors
every test module failed at collection. `allow: dependency-type: all`
puts those pins on the same weekly grouped PR as everything else.

Pushed from gojiplus/py-canon tools/fleet_dependabot_allow.py.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
