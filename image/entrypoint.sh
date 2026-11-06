#!/bin/sh
set -eu
umask 077
# 兼容从 2.0.x 升级：只在新目录尚不存在时迁移旧数据。
legacy_jenkins_dir=/lzcapp/var/jenkins_home
if [ "$JENKINS_HOME" = /lzcapp/var/jenkins ] && [ ! -e "$JENKINS_HOME" ] && [ -d "$legacy_jenkins_dir" ]; then
    mv "$legacy_jenkins_dir" "$JENKINS_HOME"
fi
mkdir -p "$JENKINS_HOME/secrets" "$JENKINS_HOME/casc" "$JENKINS_HOME/workspace" \
    "$GOPATH" "$GOCACHE" "$npm_config_cache" "$PIP_CACHE_DIR"

admin_exists=false
for user_config in "$JENKINS_HOME"/users/*/config.xml; do
    if [ -f "$user_config" ] && grep -q '<id>admin</id>' "$user_config"; then
        admin_exists=true
        break
    fi
done
reset_file="$JENKINS_HOME/secrets/reset-admin-password"
reset_requested=false
if [ -s "$reset_file" ]; then
    reset_requested=true
fi
export JENKINS_ADMIN_EXISTS="$admin_exists"
export JENKINS_ADMIN_RESET_FILE="$reset_file"
python3 - <<'PY'
import os, pathlib, secrets, string
target=pathlib.Path(os.environ['JENKINS_HOME'])/'secrets/admin-password'
source=os.environ.get('JENKINS_ADMIN_PASSWORD_FILE')
inline=os.environ.get('JENKINS_ADMIN_PASSWORD')
reset=pathlib.Path(os.environ['JENKINS_ADMIN_RESET_FILE'])
admin_exists=os.environ['JENKINS_ADMIN_EXISTS']=='true'
if source and inline:
    raise SystemExit('Set only one of JENKINS_ADMIN_PASSWORD_FILE and JENKINS_ADMIN_PASSWORD')
if reset.exists() and reset.stat().st_size:
    value=reset.read_text().strip()
elif not admin_exists and source:
    value=pathlib.Path(source).read_text().strip()
elif not admin_exists and inline:
    value=inline.strip()
else:
    value=''
if value:
    if not value or '\n' in value or '\r' in value:
        raise SystemExit('Admin password must contain a nonempty single line')
    if not target.exists() or target.read_text().strip()!=value:
        target.write_text(value+'\n')
elif not admin_exists and not target.exists():
    alphabet=string.ascii_letters+string.digits+'_'
    while True:
        value=''.join(secrets.choice(alphabet) for _ in range(16))
        if all(any(c in group for c in value) for group in [string.ascii_lowercase,string.ascii_uppercase,string.digits,'_']): break
    with target.open('x') as f: f.write(value+'\n')
if target.exists():
    os.chmod(target,0o600)
if reset.exists():
    reset.unlink()
PY
if [ "$admin_exists" = false ] || [ "$reset_requested" = true ]; then
    CASC_JENKINS_CONFIG=/opt/jenkins-custom/jenkins-bootstrap.yaml
else
    CASC_JENKINS_CONFIG=/opt/jenkins-custom/jenkins.yaml
fi
export CASC_JENKINS_CONFIG
exec /usr/bin/tini -- /usr/local/bin/jenkins.sh "$@"
