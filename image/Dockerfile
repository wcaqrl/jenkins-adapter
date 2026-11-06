ARG PYTHON_BASE_IMAGE=python:3.12-slim-trixie@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea
ARG JENKINS_BASE_IMAGE=docker.io/jenkins/jenkins:2.580.2-jdk21@sha256:19db581de85e7ce6a1ad89e55a0318d1114e4f103d2dc25493a235ff2b26d044
FROM ${PYTHON_BASE_IMAGE} AS python
FROM ${JENKINS_BASE_IMAGE}
ARG JENKINS_BASE_IMAGE
ARG BUILD_FINGERPRINT=local
LABEL org.opencontainers.image.source="https://github.com/wcaqrl/jenkins-adapter" \
      org.opencontainers.image.base.name="${JENKINS_BASE_IMAGE}" \
      peter.lazycat.build-fingerprint="${BUILD_FINGERPRINT}"
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
    git openssh-client curl ca-certificates xz-utils unzip build-essential pkg-config \
    libbz2-1.0 libffi8 libgdbm6t64 libgdbm-compat4t64 liblzma5 libncursesw6 \
    libreadline8t64 libsqlite3-0 libssl3t64 libuuid1 zlib1g libexpat1 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=python /usr/local/ /usr/local/
RUN ldconfig && python3.12 -c "import ssl, sqlite3, bz2, lzma, ctypes, readline, venv"
ARG GO_SHA256
ARG NODE_SHA256
ARG NPM_VERSION
COPY downloads/go.tar.gz /tmp/go.tar.gz
COPY downloads/node.tar.xz /tmp/node.tar.xz
RUN echo "${GO_SHA256}  /tmp/go.tar.gz" | sha256sum -c - \
    && echo "${NODE_SHA256}  /tmp/node.tar.xz" | sha256sum -c - \
    && tar -xzf /tmp/go.tar.gz -C /usr/local \
    && mkdir -p /opt/node \
    && tar -xJf /tmp/node.tar.xz -C /opt/node --strip-components=1 \
    && rm /tmp/go.tar.gz /tmp/node.tar.xz
ENV PATH="/opt/node/bin:/usr/local/go/bin:/usr/local/bin:${PATH}"
RUN npm install --global "npm@${NPM_VERSION}" && npm cache clean --force
COPY plugins.txt /usr/share/jenkins/ref/plugins.txt
USER jenkins
RUN jenkins-plugin-cli --plugin-file /usr/share/jenkins/ref/plugins.txt --latest true
USER root
COPY jenkins.yaml /opt/jenkins-custom/jenkins.yaml
COPY jenkins-bootstrap.yaml /opt/jenkins-custom/jenkins-bootstrap.yaml
COPY entrypoint.sh /usr/local/bin/jenkins-custom-entrypoint
COPY versions.lock.json /opt/jenkins-custom/versions.lock.json
RUN chmod 0755 /usr/local/bin/jenkins-custom-entrypoint
ENV JENKINS_HOME=/lzcapp/var/jenkins \
    JENKINS_JAVA_OPTS="-Djenkins.install.runSetupWizard=false -Xms256m -Xmx1536m" \
    CASC_JENKINS_CONFIG=/opt/jenkins-custom/jenkins.yaml \
    SECRETS=/lzcapp/var/jenkins/secrets \
    GOPATH=/lzcapp/cache/go \
    GOCACHE=/lzcapp/cache/go-build \
    npm_config_cache=/lzcapp/cache/npm \
    PIP_CACHE_DIR=/lzcapp/cache/pip \
    TZ=Asia/Shanghai
USER jenkins
ENTRYPOINT ["/usr/local/bin/jenkins-custom-entrypoint"]
