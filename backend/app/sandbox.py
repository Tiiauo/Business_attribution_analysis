"""Docker API access belongs to the worker, never to the web application."""

import asyncio
import hashlib
import io
import json
import tarfile
from contextlib import asynccontextmanager
from uuid import uuid4

import httpx2
from sqlalchemy.engine import make_url

from app.core.config import get_settings
from app.core.errors import ApiError
from app.core.security import log_event
from app.task_events import database_dsn

SCRIPT_VERSION = "1"
SCRIPTS = {
    "python_stats": """import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
data=json.loads(Path('/input/data.json').read_text())
values=[Decimal(str(v)) for v in data['values']]
if not values or any(not v.is_finite() for v in values): raise ValueError('Nonfinite or empty input')
total=sum(values,Decimal(0))
print(json.dumps({'count':len(values),'sum':str(total),'mean':str(total/len(values)),'min':str(min(values)),'max':str(max(values))}))
""",
    "text_search": """import json
from pathlib import Path
data=json.loads(Path('/input/data.json').read_text())
query=data['query']
matches=[{'line':i,'text':line} for i,line in enumerate(data['text'].splitlines(),1) if query.casefold() in line.casefold()]
if len(matches)>100: raise ValueError('Too many matches; narrow query')
print(json.dumps({'matches':matches},ensure_ascii=False))
""",
}


def namespace():
    identity = make_url(database_dsn())._replace(password=None).render_as_string(hide_password=False)
    return hashlib.sha256(identity.encode() + b"\0" + get_settings().master_key_file.read_bytes()).hexdigest()[:16]


@asynccontextmanager
async def docker():
    host = get_settings().docker_host
    transport = httpx2.AsyncHTTPTransport(uds=host[7:]) if host.startswith("unix://") else None
    async with httpx2.AsyncClient(
        base_url=("http://docker" if transport else host) + "/v1.45",
        transport=transport,
        timeout=10,
        follow_redirects=False,
    ) as client:
        yield client


async def api(client, method, path, **kwargs):
    response = await client.request(method, path, **kwargs)
    if response.status_code not in {200, 201, 204, 304} and not (method == "DELETE" and response.status_code == 404):
        raise ApiError(503, "SANDBOX_UNAVAILABLE", "隔离执行环境不可用，请检查 Docker、镜像和资源。")
    return response


def container_config(image, labels, volume, *, run):
    return {
        "Image": image,
        "Labels": labels,
        "NetworkDisabled": True,
        "User": "10001:10001",
        "Tty": True,
        "Cmd": ["python", "-I", "-B", "/input/script.py"] if run else ["true"],
        "WorkingDir": "/output",
        "Env": ["PYTHONIOENCODING=utf-8"],
        "HostConfig": {
            "NetworkMode": "none",
            "ReadonlyRootfs": True,
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges:true"],
            "Memory": 268435456,
            "MemorySwap": 268435456,
            "PidsLimit": 32,
            "NanoCpus": 500000000,
            "Tmpfs": {
                "/output": "rw,noexec,nosuid,nodev,size=16m,uid=10001,gid=10001,mode=0700",
                "/tmp": "rw,noexec,nosuid,nodev,size=8m,uid=10001,gid=10001,mode=0700",
            },
            "Mounts": [{"Type": "volume", "Source": volume, "Target": "/input", "ReadOnly": run}],
            "LogConfig": {"Type": "json-file", "Config": {"max-size": "1m", "max-file": "1"}},
        },
    }


async def remove_resources(containers, volume):
    # A slot remains occupied until the daemon confirms the process and its input volume are gone.
    while True:
        try:
            async with docker() as client:
                for container in containers:
                    response = await client.post(f"/containers/{container}/kill")
                    if response.status_code not in {204, 404, 409}:
                        raise OSError("Kill not confirmed")
                    await api(client, "DELETE", f"/containers/{container}", params={"force": "true", "v": "true"})
                    if (await client.get(f"/containers/{container}/json")).status_code != 404:
                        raise OSError("Container removal not confirmed")
                if volume:
                    await api(client, "DELETE", f"/volumes/{volume}")
                    if (await client.get(f"/volumes/{volume}")).status_code != 404:
                        raise OSError("Volume removal not confirmed")
            return
        except (httpx2.HTTPError, OSError, ApiError):
            log_event("sandbox_cleanup_retry")
            await asyncio.sleep(2)


async def cleanup_orphans():
    try:
        async with docker() as client:
            filters = json.dumps({"label": ["ba.worker=" + namespace()]})
            response = await api(client, "GET", "/containers/json", params={"all": "true", "filters": filters})
            containers = [row["Id"] for row in response.json()]
            volumes = (await api(client, "GET", "/volumes", params={"filters": filters})).json().get("Volumes") or []
        await remove_resources(containers, None)
        for volume in volumes:
            await remove_resources([], volume["Name"])
    except (httpx2.HTTPError, ValueError, KeyError):
        raise ApiError(503, "SANDBOX_UNAVAILABLE", "无法确认旧隔离进程已停止，执行器暂不接收任务。") from None


async def run_script(script, data, task_id, timeout, *, source=None, memory=268435456):
    token = uuid4().hex
    labels = {"ba.worker": namespace(), "ba.task": str(task_id)}
    volume = "ba-input-" + token
    output_volume = volume + "-output" if source is not None else None
    containers = []
    try:
        async with docker() as client:
            response = await api(client, "POST", "/volumes/create", json={"Name": volume, "Labels": labels})
            if response.status_code != 201:
                raise ApiError(503, "SANDBOX_UNAVAILABLE", "无法创建任务输入卷。")
            if output_volume:
                await api(client, "POST", "/volumes/create", json={"Name": output_volume, "Labels": labels})
            stage = "ba-stage-" + token
            containers.append(stage)
            config = container_config(get_settings().sandbox_image, labels, volume, run=False)
            if output_volume:
                config["HostConfig"]["Mounts"].append(
                    {"Type": "volume", "Source": output_volume, "Target": "/result", "ReadOnly": False}
                )
            response = await api(client, "POST", "/containers/create", params={"name": stage}, json=config)
            if response.status_code != 201:
                raise ApiError(503, "SANDBOX_IMAGE_MISSING", "隔离镜像不存在，请按部署说明预载固定镜像。")
            archive = io.BytesIO()
            with tarfile.open(fileobj=archive, mode="w") as bundle:
                files = [
                    ("script.py", script.encode()),
                    ("data.json", json.dumps(data, ensure_ascii=False).encode()),
                ]
                if source is not None:
                    files.append(("source", source))
                for name, content in files:
                    info = tarfile.TarInfo(name)
                    info.size, info.mode = len(content), 0o444
                    bundle.addfile(info, io.BytesIO(content))
            await api(
                client,
                "PUT",
                f"/containers/{stage}/archive",
                params={"path": "/input"},
                content=archive.getvalue(),
                headers={"Content-Type": "application/x-tar"},
            )
            if output_volume:
                output_archive = io.BytesIO()
                with tarfile.open(fileobj=output_archive, mode="w") as bundle:
                    info = tarfile.TarInfo("data")
                    info.type, info.mode, info.uid, info.gid = tarfile.DIRTYPE, 0o700, 10001, 10001
                    bundle.addfile(info)
                await api(
                    client,
                    "PUT",
                    f"/containers/{stage}/archive",
                    params={"path": "/result"},
                    content=output_archive.getvalue(),
                    headers={"Content-Type": "application/x-tar"},
                )
            await api(client, "DELETE", f"/containers/{stage}", params={"force": "true"})
            runner = "ba-run-" + token
            containers.append(runner)
            config = container_config(get_settings().sandbox_image, labels, volume, run=True)
            config["HostConfig"].update(Memory=memory, MemorySwap=memory)
            if source is not None:
                config["HostConfig"]["Tmpfs"].pop("/output")
                config["HostConfig"]["Mounts"].append(
                    {"Type": "volume", "Source": output_volume, "Target": "/output", "ReadOnly": False}
                )
            await api(
                client,
                "POST",
                "/containers/create",
                params={"name": runner},
                json=config,
            )
            await api(client, "POST", f"/containers/{runner}/start")
            async with asyncio.timeout(timeout):
                while True:
                    state = (await api(client, "GET", f"/containers/{runner}/json")).json()["State"]
                    if not state["Running"]:
                        break
                    await asyncio.sleep(0.1)
            if source is not None:
                if state["ExitCode"] != 0 or state.get("OOMKilled"):
                    raise ApiError(422, "PARSE_RESOURCE_LIMIT", "解析失败或超过 parse_max_memory_bytes，请拆分文件。")
                async with client.stream(
                    "GET", f"/containers/{runner}/archive", params={"path": "/output/data/result.json"}
                ) as response:
                    if response.status_code != 200:
                        raise ApiError(422, "PARSE_OUTPUT_MISSING", "解析输出缺失，可能超过内存或输出空间限制。")
                    output = bytearray()
                    async for chunk in response.aiter_bytes():
                        output.extend(chunk)
                        if len(output) > 96_000_000:
                            raise ApiError(422, "PARSE_OUTPUT_LIMIT", "标准化数据超过96 MB，请拆分文件。")
                with tarfile.open(fileobj=io.BytesIO(output)) as archive:
                    member = archive.getmember("result.json")
                    return archive.extractfile(member).read().decode("utf-8")
            async with client.stream(
                "GET", f"/containers/{runner}/logs", params={"stdout": "true", "stderr": "true"}
            ) as response:
                output = bytearray()
                async for chunk in response.aiter_bytes():
                    output.extend(chunk)
                    if len(output) > 1_000_000:
                        raise ApiError(422, "TOOL_OUTPUT_TOO_LARGE", "工具输出超过上限，请缩小输入。")
            if state["ExitCode"] != 0 or state.get("OOMKilled"):
                raise ApiError(422, "COMMAND_FAILED", "批准脚本执行失败或内存超限，请检查输入与转换口径。")
            return bytes(output).decode("utf-8")
    except TimeoutError:
        raise ApiError(504, "COMMAND_TIMEOUT", "批准脚本超过运行时限。") from None
    except (httpx2.HTTPError, KeyError, ValueError):
        raise ApiError(503, "SANDBOX_UNAVAILABLE", "隔离执行失败，请检查 Docker 和固定镜像。") from None
    finally:

        async def remove_all():
            await remove_resources(containers, volume)
            if output_volume:
                await remove_resources([], output_volume)

        cleanup = asyncio.create_task(remove_all())
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            await cleanup
            raise


async def run_command(command, data, task_id, timeout):
    if command not in SCRIPTS:
        raise ApiError(403, "COMMAND_NOT_ALLOWED", "该命令不在批准清单中。")
    return await run_script(SCRIPTS[command], data, task_id, timeout)
