ZPOOL=""
SERVER=""
PYTHON?=/usr/local/bin/python3
GETPYVER=${PYTHON} -c "import sys;\
  print(str(sys.version_info.major) + str(sys.version_info.minor))"
DEPS=\
  py%-build\
  py%-click\
  py%-coloredlogs\
  py%-dnspython\
  py%-fastentrypoints\
  py%-gitpython\
  py%-hatchling\
  py%-jsonschema\
  py%-netifaces\
  py%-pip\
  py%-pytest\
  py%-requests\
  py%-texttable

help:
	@echo "    depends"
	@echo "        Installs dependencies"
	@echo "    install"
	@echo "        Install iocage"
	@echo "    uninstall"
	@echo "        Remove iocage"
	@echo "    test"
	@echo "        Run unit tests with pytest"

depends:
	@(pkg -vv | grep -e "url.*/latest") > /dev/null 2>&1 || \
	  echo 'It is advised pkg url is using "latest" instead of'\
	  '"quarterly" in /etc/pkg/FreeBSD.conf.'
	@test -s ${PYTHON} || \
	  (echo "${PYTHON} not found, iocage will install python3";\
	  pkg install -q -y python3)
	@${GETPYVER} | xargs -I% -R-1 pkg install -yA ${DEPS}

install: depends
	${PYTHON} -m pip install .

uninstall:
	${PYTHON} -m pip uninstall -y iocage

test:
	pytest --zpool $(ZPOOL) --server $(SERVER)
