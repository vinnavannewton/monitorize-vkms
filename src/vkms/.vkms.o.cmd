savedcmd_vkms.o := ld -m elf_x86_64 -z noexecstack --no-warn-rwx-segments   -r -o vkms.o @vkms.mod  ; /usr/src/kernels/7.1.10-200.fc44.x86_64/tools/objtool/objtool --hacks=jump_label --hacks=noinstr --hacks=skylake --ibt --orc --retpoline --rethunk --sls --static-call --uaccess --prefix=16  --link  --module vkms.o

vkms.o: $(wildcard /usr/src/kernels/7.1.10-200.fc44.x86_64/tools/objtool/objtool)
