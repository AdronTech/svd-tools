#!/usr/bin/env python
#
# Copyright 2019 Ludovic Barre <1udovic.6arre@gmail.com>
#
# This file is part of svd-tools.
#
# svd-tools is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 2 of the License, or
# (at your option) any later version.
#
# svd-tools is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with svd-tools.  If not, see <https://www.gnu.org/licenses/>.

import re
import gdb
from terminaltables import AsciiTable
from cmsis_svd.parser import SVDParser, SVDAccessType
from textwrap import wrap
from colorama import Fore, Style
import traceback


def error(msg):
    return f"{Fore.RED}{msg}{Style.RESET_ALL}"


def warning(msg):
    return f"{Fore.YELLOW}{msg}{Style.RESET_ALL}"


def info(msg):
    return f"{Fore.BLUE}{msg}{Style.RESET_ALL}"


def highlight(msg):
    return f"{Fore.CYAN}{msg}{Style.RESET_ALL}"


def colorize_prefix(prefix, txt):
    if not txt.startswith(prefix):
        return txt
    return f"{highlight(prefix)}{txt[len(prefix) :]}"


def heading(columns):
    return [f"{Fore.BLACK}{col}{Style.RESET_ALL}" for col in columns]


def allowed_to_read(access: SVDAccessType | None):
    return access in [
        None,
        SVDAccessType.READ_ONLY,
        SVDAccessType.READ_WRITE,
        SVDAccessType.READ_WRITE_ONCE,
    ]


def allowed_to_write(access: SVDAccessType | None):
    return access in [
        None,
        SVDAccessType.WRITE_ONLY,
        SVDAccessType.READ_WRITE,
        SVDAccessType.WRITE_ONCE,
        SVDAccessType.READ_WRITE_ONCE,
    ]


def get_access_str(access: SVDAccessType | None):
    if access is None:
        return SVDAccessType.READ_WRITE.value
    return access.value


def parse_args(raw: str):
    """Parse a raw argument string into a list using gdb facilities.

    Using gdb.string_to_argv ensures proper handling of quoting and
    escaping (spaces inside quotes, etc.) compared to naive split.
    """
    try:
        return gdb.string_to_argv(raw)
    except Exception:
        # Fall back to an empty list on unexpected parsing failure
        return []


class NotReadableError(Exception):
    def __init__(self):
        super().__init__("Register not readable, check access type.")


class NotWritableError(Exception):
    def __init__(self):
        super().__init__("Register not writable, check access type.")


class GdbSvd(gdb.Command):
    """The CMSIS SVD (System View Description) inspector commands

    This allows easy access to all peripheral registers supported by the system
    in the GDB debug environment

    svd [filename] load an SVD file and to create the command for inspecting
    that object
    """

    def __init__(self):
        gdb.Command.__init__(
            self, "svd", gdb.COMMAND_DATA, gdb.COMPLETE_FILENAME, prefix=True
        )

    def invoke(self, arg, from_tty):
        try:
            argv = gdb.string_to_argv(arg)
            if len(argv) != 1:
                raise Exception("Invalid parameter")

            pathfile = argv[0]
            gdb.write(f"Svd Loading {pathfile} ")
            parser = SVDParser.for_xml_file(pathfile)
            device = parser.get_device()

            peripherals = dict(
                (peripheral.name, peripheral) for peripheral in device.peripherals
            )

        except Exception as inst:
            gdb.write(f"\n{inst}\n")
            gdb.execute("help svd")
        except IOError:
            gdb.write("\nFailed to load SVD file\n")
        else:
            gdb.write("Done\n")

            GdbSvdGetCmd(device, peripherals)
            GdbSvdSetCmd(device, peripherals)
            GdbSvdInfoCmd(device, peripherals)
            # GdbSvdDumpCmd(device, peripherals)


if __name__ == "__main__":
    GdbSvd()


class GdbSvdCmd(gdb.Command):
    def __init__(self, device, peripherals):
        self.device = device
        self.peripherals = peripherals
        self.column_with = 100
        version = gdbserver = []

        try:
            version = gdb.execute("monitor version", False, True)
        except Exception:
            pass

        try:
            gdbserver = gdb.execute("monitor gdbserver status", False, True)
        except Exception:
            pass

        if "Open On-Chip Debugger" in version:
            self.read_cmd = "monitor mdw phys {address:#x}"
            self.write_cmd = "monitor mww phys {address:#x} {value:#x}"

        elif "gdbserver for" in gdbserver:
            self.read_cmd = "monitor rw {address:#x}"
            self.write_cmd = "monitor ww {address:#x} {value:#x}"
        else:
            self.read_cmd = "x /x {address:#x}"
            self.write_cmd = "set *(int *){address:#x}={value:#x}"

    def complete(self, text, word):
        args = parse_args(str(text))
        nb_args = len(args)

        if nb_args == 1:
            peripheral_matches = filter(
                lambda x: x.upper().startswith(args[0].upper()), self.peripherals
            )
            return list(peripheral_matches)

        peripheral_name = args[0].upper()
        peripheral = self.peripherals[peripheral_name]
        register_names = [reg.name for reg in peripheral.registers]

        if nb_args == 2 and register_names:
            register_matches = filter(
                lambda x: x.upper().startswith(args[1].upper()), register_names
            )
            return list(register_matches)

        register_name = args[1].upper()
        register = [r for r in peripheral.registers if r.name == register_name][0]
        field_names = [field.name for field in register.fields]

        if nb_args == 3 and field_names:
            field_matches = filter(
                lambda x: x.upper().startswith(args[2].upper()), field_names
            )
            return list(field_matches)

        return gdb.COMPLETE_NONE

    def print_desc_peripherals(self, peripherals, peripheral_prefix=""):
        table_show = []
        table_show.append(heading(["name", "base", "access", "description"]))
        for peripheral in peripherals:
            name = colorize_prefix(peripheral_prefix, peripheral.name)

            desc = "\n".join(wrap(peripheral.description, self.column_with))
            table_show.append(
                [
                    name,
                    f"{peripheral.base_address:#08x}",
                    get_access_str(peripheral.access),
                    desc,
                ]
            )

        desc_table = AsciiTable(table_show, title=" Peripherals ")
        gdb.write(f"{desc_table.table}\n")

    def print_desc_registers(self, breadcrumbs, registers, register_prefix=""):
        table_rows = []
        table_rows.append(heading(["name", "address", "access", "description"]))
        for register in registers:
            name = colorize_prefix(register_prefix, register.name)

            addr = register.parent.base_address + register.address_offset
            desc = "\n".join(wrap(register.description, self.column_with))
            table_rows.append(
                [name, f"{addr:#08x}", get_access_str(register.access), desc]
            )

        desc_table = AsciiTable(
            table_rows, title=f" {highlight(breadcrumbs)} Registers "
        )
        gdb.write(f"{desc_table.table}\n")

    def print_desc_fields(self, breadcrumbs, fields, field_prefix=""):
        table_rows = []
        table_rows.append(heading(["name", "[msb:lsb]", "access", "description"]))
        for field in fields:
            name = colorize_prefix(field_prefix, field.name)

            lsb = field.bit_offset
            msb = field.bit_offset + field.bit_width - 1
            bit_range = f"[{msb}:{lsb}]"
            desc = "\n".join(wrap(field.description, self.column_with))
            table_rows.append([name, bit_range, get_access_str(field.access), desc])

        desc_table = AsciiTable(table_rows, title=f" {highlight(breadcrumbs)} Fields ")
        gdb.write(f"{desc_table.table}\n")

    def get_field_name(self, field):
        lsb = field.bit_offset
        msb = field.bit_offset + field.bit_width - 1
        return f"{field.name}[{msb}:{lsb}]"

    def get_field_value(self, bit_offset, bit_width, reg_values):
        return (reg_values >> bit_offset) & ((1 << bit_width) - 1)

    def get_field_string(self, field, reset_value, value):
        field_reset_value = self.get_field_value(
            field.bit_offset, field.bit_width, reset_value
        )

        field_name = self.get_field_name(field)
        field_value = self.get_field_value(field.bit_offset, field.bit_width, value)
        field_string = f"{field_name}={field_value:#x}({field_reset_value:#x})"

        if field_value != field_reset_value:
            field_string = highlight(field_string)

        return field_string

    def get_register_row(self, register, register_prefix=""):
        name = colorize_prefix(register_prefix, register.name)
        reset_value = register.reset_value
        addr = register.parent.base_address + register.address_offset

        try:
            value = self.read(register)
        except NotReadableError:
            return name, addr, warning(get_access_str(register.access)), ""
        except Exception as err:
            return name, addr, error("Error"), error(str(err))

        field_str_parts = []
        for field in register.fields:
            f_str_part = self.get_field_string(field, reset_value, value)
            field_str_parts.append(f_str_part)

        field_str = " ".join(field_str_parts)
        val_str = f"{value:#x}({reset_value:#x})"
        if value != reset_value:
            val_str = highlight(val_str)

        return name, addr, val_str, "\n".join(wrap(field_str, self.column_with))

    def print_registers(self, breadcrumbs, registers, register_prefix=""):
        regs_table = []
        regs_table.append(heading(["name", "address", "value", "fields"]))

        for register in registers:
            regs_table.append(
                self.get_register_row(register, register_prefix=register_prefix)
            )

        rval_table = AsciiTable(
            regs_table, title=f" {highlight(breadcrumbs)} Registers "
        )

        gdb.write(f"{rval_table.table}\n")

    def set_register(self, register, value, field=None):
        val = value
        if field is not None:
            max_val = (1 << field.bit_width) - 1
            if value > max_val:
                raise Exception("Invalid value, > max of field")

            mask = max_val << field.bit_offset

            # read register value with gdb
            val = self.read(register)

            val &= ~mask
            val |= value << field.bit_offset

        # write val to target
        self.write(register, val)

    def read(self, register):
        """Read register and return an integer"""
        # access could be not defined for a register
        if not allowed_to_read(register.access):
            raise NotReadableError()

        addr = register.parent.base_address + register.address_offset
        cmd = self.read_cmd.format(address=addr)
        pattern = re.compile(r"(?P<ADDR>\w+):( *?(?P<VALUE>[a-f0-9]+))")

        try:
            match = re.search(pattern, gdb.execute(cmd, False, True))
            val = int(match.group("VALUE"), 16)
        except Exception as err:
            # if openocd can't access to addr => data abort
            return err
        return val

    def write(self, register, val):
        """Write data to memory"""
        if not allowed_to_write(register.access):
            raise NotWritableError()

        addr = register.parent.base_address + register.address_offset
        cmd = self.write_cmd.format(address=addr, value=val)

        gdb.execute(cmd, False, True)


# sub commands
class GdbSvdGetCmd(GdbSvdCmd):
    """Get register(s) value(s): svd get [peripheral] [register]"""

    def __init__(self, device, peripherals):
        GdbSvdCmd.__init__(self, device, peripherals)
        gdb.Command.__init__(self, "svd get", gdb.COMMAND_DATA)

    def complete(self, text, word):
        args = parse_args(str(text))
        if len(args) > 2:
            return gdb.COMPLETE_NONE

        return GdbSvdCmd.complete(self, text, word)

    def invoke(self, arg, from_tty):
        try:
            args = parse_args(str(arg))
            if len(args) > 2:
                gdb.write(error("Invalid parameter\n"))
                gdb.execute("help svd get")
                return

            peripheral_arg = args[0].upper()
            peripheral_matches = list(
                filter(
                    lambda x: x.name.startswith(peripheral_arg),
                    self.device.peripherals,
                )
            )

            if len(peripheral_matches) == 0:
                gdb.write(error(f"No peripheral with prefix '{peripheral_arg}'\n"))
                GdbSvdCmd.print_desc_peripherals(self, self.device.peripherals)
                return

            if len(peripheral_matches) > 1:
                gdb.write(
                    info(
                        f"Multiple peripherals with prefix '{peripheral_arg}' found, please specify more!\n"
                    )
                )
                GdbSvdCmd.print_desc_peripherals(
                    self, peripheral_matches, peripheral_prefix=peripheral_arg
                )
                return

            # this means we have only one peripheral
            peripheral = peripheral_matches[0]
            breadcrumbs = f"{peripheral.name}"

            if peripheral.name != peripheral_arg:
                gdb.write(
                    warning(
                        f"Only one peripheral with prefix '{peripheral_arg}' found: {peripheral.name}\n"
                    )
                )

            if len(args) == 1:
                GdbSvdCmd.print_registers(self, breadcrumbs, peripheral.registers)
                return

            register_arg = args[1].upper()
            register_matches = list(
                filter(lambda x: x.name.startswith(register_arg), peripheral.registers)
            )

            if len(register_matches) == 0:
                gdb.write(
                    error(
                        f"No registers with prefix '{register_arg}' for peripheral '{peripheral.name}'\n"
                    )
                )
                GdbSvdCmd.print_desc_registers(self, breadcrumbs, peripheral.registers)
                return

            GdbSvdCmd.print_registers(
                self, breadcrumbs, register_matches, register_prefix=register_arg
            )
        except Exception as inst:
            gdb.write(error(f"{inst}\n"))
            traceback.print_exc()


class GdbSvdSetCmd(GdbSvdCmd):
    """Set register value: svd set <peripheral> <register> [field] <value>"""

    def __init__(self, device, peripherals):
        GdbSvdCmd.__init__(self, device, peripherals)
        gdb.Command.__init__(self, "svd set", gdb.COMMAND_DATA)

    def complete(self, text, word):
        args = parse_args(str(text))
        if len(args) > 3:
            return gdb.COMPLETE_NONE

        return GdbSvdCmd.complete(self, text, word)

    def invoke(self, arg, from_tty):
        args = parse_args(str(arg))

        try:
            peripheral_name = args[0].upper()
            peripheral = self.peripherals[peripheral_name]
        except Exception:
            gdb.write("Invalid peripheral name\n")
            GdbSvdCmd.print_desc_peripherals(self, self.device.peripherals)
            return

        if len(args) < 3 or len(args) > 4:
            gdb.write("Invalid parameter\n")
            gdb.execute("help svd set")
            return

        try:
            register_name = args[1].upper()
            register = [r for r in peripheral.registers if r.name == register_name][0]
            field = None
            if len(args) == 4:
                field_name = args[2].upper()
                field = [f for f in register.fields if f.name == field_name][0]
                value = int(args[3], 16)
            else:
                value = int(args[2], 16)

            GdbSvdCmd.set_register(self, register, value, field)

        except Exception as inst:
            gdb.write(f"{inst}\n")

        except Exception:
            gdb.write("Error cannot set the value\n")


class GdbSvdInfoCmd(GdbSvdCmd):
    """Info on Peripheral|register|field: svd info <peripheral> [register] [field]"""

    def __init__(self, device, peripherals):
        GdbSvdCmd.__init__(self, device, peripherals)
        gdb.Command.__init__(self, "svd info", gdb.COMMAND_DATA)

    def complete(self, text, word):
        args = parse_args(str(text))
        if len(args) > 3:
            return gdb.COMPLETE_NONE

        return GdbSvdCmd.complete(self, text, word)

    def invoke(self, arg, from_tty):
        try:
            if arg == "":
                GdbSvdCmd.print_desc_peripherals(self, self.device.peripherals)
                return

            args = parse_args(str(arg))
            if not 1 <= len(args) <= 3:
                gdb.write(error("Invalid parameter\n"))
                gdb.execute("help svd info")
                return

            peripheral_arg = args[0].upper()
            peripheral_matches = list(
                filter(
                    lambda x: x.name.startswith(peripheral_arg),
                    self.device.peripherals,
                )
            )

            if len(peripheral_matches) == 0:
                gdb.write(error(f"No peripheral with prefix '{peripheral_arg}'\n"))
                GdbSvdCmd.print_desc_peripherals(self, self.device.peripherals)
                return

            if len(peripheral_matches) > 1:
                gdb.write(
                    info(
                        f"Multiple peripherals with prefix '{peripheral_arg}' found, please specify more!\n"
                    )
                )
                GdbSvdCmd.print_desc_peripherals(
                    self, peripheral_matches, peripheral_prefix=peripheral_arg
                )
                return

            # this means we have only one peripheral
            peripheral = peripheral_matches[0]
            breadcrumbs = f"{peripheral.name}"

            if peripheral.name != peripheral_arg:
                gdb.write(
                    warning(
                        f"Only one peripheral with prefix '{peripheral_arg}' found: {peripheral.name}\n"
                    )
                )

            if len(args) == 1:
                GdbSvdCmd.print_desc_registers(self, breadcrumbs, peripheral.registers)
                return

            register_arg = args[1].upper()
            register_matches = list(
                filter(lambda x: x.name.startswith(register_arg), peripheral.registers)
            )

            if len(register_matches) == 0:
                gdb.write(
                    error(
                        f"No registers with prefix '{register_arg}' for peripheral '{peripheral.name}'\n"
                    )
                )
                GdbSvdCmd.print_desc_registers(self, breadcrumbs, peripheral.registers)
                return

            if len(register_matches) > 1:
                gdb.write(
                    info(
                        f"Multiple registers with prefix '{register_arg}' found for peripheral '{peripheral.name}', please specify more!\n"
                    )
                )
                GdbSvdCmd.print_desc_registers(
                    self, breadcrumbs, register_matches, register_prefix=register_arg
                )
                return

            # this means we have only one register
            register = register_matches[0]
            breadcrumbs += f":{register.name}"

            if register.name != register_arg:
                gdb.write(
                    warning(
                        f"Only one register with prefix '{register_arg}' found: {register.name}\n"
                    )
                )

            if len(args) == 2:
                GdbSvdCmd.print_desc_fields(self, breadcrumbs, register.fields)
                return

            field_arg = args[2].upper()
            field_matches = list(
                filter(lambda x: x.name.startswith(field_arg), register.fields)
            )

            if len(field_matches) == 0:
                gdb.write(
                    error(
                        f"No fields with prefix '{field_arg}' in register '{register.name}'\n"
                    )
                )
                GdbSvdCmd.print_desc_fields(self, breadcrumbs, register.fields)
                return

            GdbSvdCmd.print_desc_fields(
                self, breadcrumbs, field_matches, field_prefix=field_arg
            )

        except Exception as inst:
            gdb.write(error(f"{inst}\n"))
            traceback.print_exc()


# class GdbSvdDumpCmd(GdbSvdCmd):
#     """Get register(s) value(s): svd dump <filename> [peripheral]"""

#     def __init__(self, device, peripherals):
#         GdbSvdCmd.__init__(self, device, peripherals)
#         gdb.Command.__init__(self, "svd dump", gdb.COMMAND_DATA)

#     def complete(self, text, word):
#         args = parse_args(str(text))
#         nb_args = len(args)

#         if nb_args == 1:
#             return gdb.COMPLETE_FILENAME

#         if nb_args > 2:
#             return gdb.COMPLETE_NONE

#         # remove first argument <filename>
#         args.pop(0)

#         return GdbSvdCmd.complete(self, " ".join(args), word)

#     def invoke(self, arg, from_tty):
#         args = parse_args(str(arg))
#         if len(args) < 1 or len(args) > 2:
#             gdb.write("Invalid parameter\n")
#             gdb.execute("help svd dump")
#             return
#         try:
#             output_file_name = args[0]
#             gdb.write(f"Print to file: {output_file_name}\n")

#             if len(args) >= 2:
#                 periph_name = args[1].upper()
#                 periphs = list(
#                     filter(
#                         lambda x: x.name.startswith(periph_name),
#                         self.device.peripherals,
#                     )
#                 )
#                 regs = None
#             else:
#                 periphs = self.device.peripherals

#             try:
#                 file_object = open(output_file_name, "w")
#                 file_object.write("Registers Dump\n")
#                 file_object.close()
#                 for per in periphs:
#                     regs = per.registers
#                     GdbSvdCmd.print_registers(self, per, regs, output_file_name, False)
#             except:
#                 gdb.write(f"Error writting to file: {output_file_name}\n")
#         except:
#             gdb.write("Error cannot dump registers\n")
