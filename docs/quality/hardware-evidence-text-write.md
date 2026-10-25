# Hardware evidence: writing a text setting

> **Verified on hardware (25 October 2026, gateway firmware 1.21.30):** the
> gateway accepts a **text value** in a ReCom configuration write
> (`{"key": "<key>", "value": "<text>"}`), applies it on the device, and the
> value is confirmed by reading the configuration back. This is the write path
> the select entities use. It was verified for **`ledColor` on one D2-01-01
> switch** and for nothing else.

This page is the evidence behind the select entities of switches (operation
mode, LED colour, LED channel, external input function). Until now only numeric
writes had been verified on hardware (vertical movement time and maximum holding
time of one blind each).

## What was done

One D2-01-01 switch, setting `ledColor` (cosmetic: only the LED of the switch).
Every step was a single request without retry. The whole configuration (11
parameters) was read from the device first and used as the reference.

| Step | Request | Gateway answer | Configuration read back from the device |
|------|---------|----------------|------------------------------------------|
| 0 | read `GET /devices/{id}/configuration` (radio) | 200 | reference: `ledColor = off` |
| 1 | `PUT` `ledColor = off` (the current value, no change) | 200 | all 11 parameters identical to the reference |
| 2 | `PUT` `ledColor = green` | 200 | `ledColor = green` (*Green LED on*), the other 10 unchanged |
| 3 | `PUT` `ledColor = off` (restore) | 200 | all 11 parameters identical to the reference |

Payload of every write:

```json
{"configuration": {"parameters": [{"key": "ledColor", "value": "green"}]}}
```

Between a write and the read back were about 5 to 6 seconds. The answer
`200` of the gateway only means that the telegram was sent; the read back from
the device is what confirms the value, which is how the integration adopts a
value too.

## What this shows

- The text format `{"key": ..., "value": "<text>"}` is accepted by the gateway,
  also for a value that differs from the stored one.
- A write changes only the named parameter; the other parameters stayed
  identical.
- Writing the old value back restores the device completely.

## What is not verified

- Other text parameters: `operationMode`, `functionExternalInput`, `ledChannel`.
- Other device types (D2-01-11 switches, blinds, dimmers) and any numeric write
  other than the two named above.
- How the gateway answers an invalid text value.
- `functionRockerA/B` stay out of the selects on purpose: the 17 D2-01-01
  devices of the test installation report the raw number `0` for rocker A, which
  the catalog (`channel1`, `inactive`, default `3`) does not list, so a choice
  could not be mapped back.

## For the maintainer

- The "not tested on hardware" note for the selects in
  [Devices](../devices.md) is narrowed to the parameters above.
- The remaining select parameters should be verified the same way, one
  parameter at a time, with the full configuration read before and after, and
  an unchanged write first. `operationMode` and `functionExternalInput` change
  how a switch behaves, so they belong at the end.
- The gateway itself reads the configurations at night (about 03:10), so a test
  is best made at another time.
