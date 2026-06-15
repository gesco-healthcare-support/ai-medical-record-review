# How to add a route / blueprint

Routes live in `mrr_ai/blueprints/`, grouped by area. To add one:

1. **Pick or create a blueprint module.** Add to an existing area file if it fits;
   otherwise create `mrr_ai/blueprints/<area>.py`:

   ```python
   from flask import Blueprint, request
   from mrr_ai import state                 # shared state (read/write as state.x)
   from mrr_ai.services.<svc> import <fn>   # business logic lives in services/

   bp = Blueprint("<area>", __name__)

   @bp.route("/myroute", methods=["POST"])
   def myroute():
       ...
   ```

2. **Register it** (only if you created a new module) in
   `mrr_ai/blueprints/__init__.py`: import `bp` and add it to the tuple in
   `register_blueprints`.

3. **Rules**
   - Keep routes thin; put logic in `services/` (no Flask imports there) so it is testable.
   - Access shared state as `state.<name>` (never `from mrr_ai.state import name`).
   - Use `current_app.config["UPLOAD_FOLDER"]`, not a hardcoded path.

4. **Test** - add an integration test in `tests/integration/` that hits the route via the
   test client with externals mocked, and assert the response + any `state` change.
