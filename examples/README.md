# Example rooms

These JSON files are complete request bodies for `POST /api/rooms`. Start DutyBell, then send one
from a trusted terminal. If room creation is protected, add the `X-DutyBell-Create-Token` header.

```bash
curl --fail-with-body http://127.0.0.1:8742/api/rooms \
  --header "Content-Type: application/json" \
  --data @examples/create-dog-break.json
```

The response contains the only plaintext access key. Store the private join URL safely and do not
commit it. The dog-break example repeats and rotates after every acknowledgement. The laundry
example ends after one acknowledgement and has no participant rotation.

To exercise the same behavior without HTTP:

```bash
dutybell create "Dog break" --seconds 7200 --participants "Alex,Sam" --start
```
