# NineData OpenAPI Authentication

## Headers

Every OpenAPI request must include:

- `access-key-id`: AccessKeyId issued by NineData.
- `timestamp`: UTC timestamp in `yyyy-MM-dd'T'HH:mm:ssZ` format.
- `signature`: SHA256 signature.
- `Content-Type: application/json`: required for POST requests.

## Signature

Signature payload:

```text
path + "/" + AccessKeySecret + "&" + timestamp
```

Example:

```bash
signature=$(echo -n "/openapi/v1/sql/execute/$accessKeySecret&$timestamp" | sha256sum | awk '{print $1}')
```

## Configuration Location

Keep personal runtime configuration outside the skill repository. The default path is:

```text
${XDG_CONFIG_HOME:-$HOME/.config}/addx/ninedata/config.json
```

Create it from the repository template:

```bash
config_dir="${XDG_CONFIG_HOME:-$HOME/.config}/addx/ninedata"
mkdir -p "$config_dir"
cp config.example.json "$config_dir/config.json"
chmod 600 "$config_dir/config.json"
```

Minimal configuration:

```json
{
  "endpoint": "https://your-ninedata-domain.example.com",
  "accessKeyId": "replace-with-access-key-id",
  "accessKeySecret": "replace-with-access-key-secret",
  "defaultDsId": "replace-with-default-datasource-id",
  "defaultDbName": "replace-with-default-database-name",
  "defaultSchemaName": "replace-with-default-schema-name",
  "source": "NINEDATA_SKILL",
  "defaultPageSize": 50,
  "defaultLanguage": "enus"
}
```

`defaultDsId`, `defaultDbName`, and `defaultSchemaName` are optional for datasource listing, but recommended. `sql-execute` uses them when `--datasource-id`, `--database-name`, or `--schema-name` are not provided.

OpenAPI credentials can instead be injected through environment variables. Environment variables take precedence over the JSON fields:

```bash
export NINEDATA_API_KEY="<access-key-id>"
export NINEDATA_SECRET_KEY="<access-key-secret>"
```

For a non-default external file, use `--config` or `NINEDATA_SKILL_CONFIG`. The resolution order is `--config`, `NINEDATA_SKILL_CONFIG`, then the default user configuration path.

## Security Requirements

- Do not print `accessKeySecret`.
- Do not pass AK/SK values in chat.
- Do not place a real `config.json` in the skill repository.
- Prefer `NINEDATA_API_KEY` and `NINEDATA_SECRET_KEY`; if AK/SK are stored in JSON, keep that file outside the skill repository.
- Set the configuration file to `chmod 600` when possible.
