# SPDX-License-Identifier: GPL-2.0+

KDIR ?= /lib/modules/$(shell uname -r)/build

FEATURE_HEADER := $(CURDIR)/vkms_oot_features.h
ATOMIC_API_PROBE := $(CURDIR)/../../scripts/probe-drm-atomic-api.sh

obj-m := vkms.o

vkms-y := \
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

$(FEATURE_HEADER): FORCE $(ATOMIC_API_PROBE)
	$(SHELL) $(ATOMIC_API_PROBE) --kdir "$(KDIR)" --output "$@"

clean:
	$(MAKE) -C $(KDIR) M=$(CURDIR) clean
	$(RM) $(FEATURE_HEADER)

FORCE:

.PHONY: all clean FORCE
