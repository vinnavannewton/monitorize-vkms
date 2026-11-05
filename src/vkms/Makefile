# SPDX-License-Identifier: GPL-2.0+

KDIR ?= /lib/modules/$(shell uname -r)/build

FEATURE_HEADER := $(CURDIR)/vkms_oot_features.h
ATOMIC_API_PROBE := $(CURDIR)/../../scripts/probe-drm-atomic-api.sh

ifneq ($(src),)
# Keep the module metadata in sync with the release used by DKMS.
monitorize_vkms_version := $(strip $(shell cat "$(src)/../../VERSION"))
ifeq ($(monitorize_vkms_version),)
$(error Missing Monitorize VKMS release version)
endif
CFLAGS_vkms_drv.o += -DMONITORIZE_VKMS_VERSION='"$(monitorize_vkms_version)"'
endif

obj-m := monitorize_vkms.o

monitorize_vkms-y := \
	vkms_drv.o \
	vkms_plane.o \
	vkms_output.o \
	vkms_formats.o \
	vkms_crtc.o \
	vkms_composer.o \
	vkms_writeback.o \
	vkms_connector.o \
	vkms_config.o \
	vkms_configfs.o \
	vkms_colorop.o \
	vkms_luts.o

all: $(FEATURE_HEADER)
	$(MAKE) -C $(KDIR) M=$(CURDIR) modules
	@symbols="$$(nm -a monitorize_vkms.ko)" || { \
		echo "could not inspect monitorize_vkms.ko exports" >&2; \
		exit 1; \
	}; \
	if printf '%s\n' "$$symbols" | grep -Eq '__ksymtab'; then \
		echo "monitorize_vkms.ko must not export kernel symbols" >&2; \
		exit 1; \
	fi

$(FEATURE_HEADER): FORCE $(ATOMIC_API_PROBE)
	$(SHELL) $(ATOMIC_API_PROBE) --kdir "$(KDIR)" --output "$@"

clean:
	$(MAKE) -C $(KDIR) M=$(CURDIR) clean
	$(RM) $(FEATURE_HEADER)

FORCE:

.PHONY: all clean FORCE
