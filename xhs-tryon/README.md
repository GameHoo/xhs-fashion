# fashn-tryon

AI virtual try-on CLI for the FASHN API.

## Requirements

- Python 3.11+
- `FASHN_API_KEY` exported in the environment

## Usage

```bash
source .env
fashn-tryon run \
  --user-image /abs/user.jpg \
  --model-image /abs/look.jpg \
  --output-dir /abs/out \
  --json
```

The CLI creates a resumable run directory with prepared inputs, manifests, results, and downloaded outputs.
