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
            GdbSvdDumpCmd(device, peripherals)


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
        except:
            pass

        try:
            gdbserver = gdb.execute("monitor gdbserver status", False, True)
        except:
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
        args = str(text).split(" ")
        nb_args = len(args)

        if nb_args == 1:
            filt = filter(
                lambda x: x.upper().startswith(args[0].upper()), self.peripherals
            )
            return list(filt)

        periph_name = args[0].upper()
        periph = self.peripherals[periph_name]
        reg_names = [reg.name for reg in periph.registers]

        if nb_args == 2 and reg_names:
            filt = filter(lambda x: x.upper().startswith(args[1].upper()), reg_names)
            return list(filt)

        reg_name = args[1].upper()
        reg = [r for r in periph.registers if r.name == reg_name][0]
        field_names = [field.name for field in reg.fields]

        if nb_args == 3 and field_names:
            filt = filter(lambda x: x.upper().startswith(args[2].upper()), field_names)
            return list(filt)

        return gdb.COMPLETE_NONE

    def get_registers_val(self, peripheral, registers):
        registers_val = []

        for reg in registers:
            addr = peripheral.base_address + reg.address_offset

            fval = ""
            val = 0
            v_err = None
            try:
                val = self.read(peripheral, reg)

                if val is not None:
                    fval = self.get_fields_val(reg.fields, val)
            except Exception as err:
                v_err = str(err)

            addr = f"{addr:#08x}"
            registers_val += [
                {
                    "name": reg.name,
                    "addr": addr,
                    "value": val,
                    "access": reg.access,
                    "reset_value": reg.reset_value,
                    "error": v_err,
                    "fields": fval,
                }
            ]

        return registers_val

    def get_fields_val(self, fields, reg_values):
        fields_val = []

        for f in fields:
            lsb = f.bit_offset
            msb = f.bit_offset + f.bit_width - 1
            fname = f"{f.name}[{msb}:{lsb}]"
            fieldval = (reg_values >> lsb) & ((1 << f.bit_width) - 1)
            fields_val += [
                {
                    "name": fname,
                    "bit_offset": f.bit_offset,
                    "bit_width": f.bit_width,
                    "value": fieldval,
                }
            ]

        return fields_val

    def print_desc_peripherals(self, peripherals, periph_prefix=""):
        table_show = []
        table_show.append(heading(["name", "base", "access", "description"]))
        for peripheral in peripherals:
            name = colorize_prefix(periph_prefix, peripheral.name)

            desc = "\n".join(wrap(peripheral.description, self.column_with))
            table_show.append(
                [name, f"{peripheral.base_address:#08x}", peripheral.access, desc]
            )

        desc_table = AsciiTable(table_show, title=" Peripherals ")
        gdb.write(f"{desc_table.table}\n")

    def print_desc_registers(self, periph_name, registers, reg_prefix=""):
        table_rows = []
        table_rows.append(heading(["name", "address", "access", "description"]))
        for register in registers:
            name = colorize_prefix(reg_prefix, register.name)

            addr = register.parent.base_address + register.address_offset
            desc = "\n".join(wrap(register.description, self.column_with))
            table_rows.append([name, f"{addr:#08x}", register.access.value, desc])

        desc_table = AsciiTable(
            table_rows, title=f" {highlight(periph_name)} Registers "
        )
        gdb.write(f"{desc_table.table}\n")

    def print_desc_fields(self, reg_name, fields, field_prefix=""):
        table_rows = []
        table_rows.append(heading(["name", "[msb:lsb]", "access", "description"]))
        for field in fields:
            name = colorize_prefix(field_prefix, field.name)

            lsb = field.bit_offset
            msb = field.bit_offset + field.bit_width - 1
            bit_range = f"[{msb}:{lsb}]"
            desc = "\n".join(wrap(field.description, self.column_with))
            table_rows.append([name, bit_range, field.access.value, desc])

        desc_table = AsciiTable(table_rows, title=f" {highlight(reg_name)} Fields ")
        gdb.write(f"{desc_table.table}\n")

    def print_registers(
        self, peripheral, registers, output_file_name="None", syntax_highlighting=True
    ):
        regs_table = []
        reg_val = []
        regs_table.append(["name", "address", "value", "fields"])

        reg_val += self.get_registers_val(peripheral, registers)

        for r in reg_val:
            f_str = []
            fields = r["fields"]
            if fields is not None:
                for f in fields:
                    field_reset_value = r["reset_value"] >> f["bit_offset"] & (
                        (1 << f["bit_width"]) - 1
                    )

                    if syntax_highlighting and f["value"] != field_reset_value:
                        f_str.append("\033[94m{name}={value:#x}\033[0m".format(**f))
                    else:
                        f_str.append("{name}={value:#x}".format(**f))

            f_str = "\n".join(wrap(" ".join(f_str), self.column_with))

            val_str = ""
            if r["error"] is not None:
                val_str = f"\033[91m{r['error']}\033[0m"
            elif r["value"] is None:
                val_str = r["access"].value
            else:
                if syntax_highlighting and r["value"] != r["reset_value"]:
                    val_str = "\033[94m{value:#x}({reset_value:#x})\033[0m".format(**r)
                else:
                    val_str = "{value:#x}({reset_value:#x})".format(**r)

            regs_table.append([r["name"], r["addr"], val_str, f_str])
        rval_table = AsciiTable(regs_table, title=peripheral.name)

        if output_file_name == "None":
            gdb.write(f"{rval_table.table}\n")
        else:
            try:
                file_object = open(output_file_name, "a")
                file_object.write(f"{rval_table.table}\n")
                file_object.close()
            except:
                gdb.write("Error writting to file \n")

    def set_register(self, peripheral, register, value, field=None):
        val = value
        if field is not None:
            max_val = (1 << field.bit_width) - 1
            if value > max_val:
                raise Exception("Invalid value, > max of field")

            mask = max_val << field.bit_offset

            # read register value with gdb
            val = self.read(peripheral, register)
            if val is None:
                raise Exception("Register not readable")

            val &= ~mask
            val |= value << field.bit_offset

        # write val to target
        self.write(peripheral, register, val)

    def read(self, peripheral, register):
        """Read register and return an integer"""
        # access could be not defined for a register
        if register.access in [
            None,
            SVDAccessType.READ_ONLY,
            SVDAccessType.READ_WRITE,
            SVDAccessType.READ_WRITE_ONCE,
        ]:
            addr = peripheral.base_address + register.address_offset
            cmd = self.read_cmd.format(address=addr)
            pattern = re.compile(r"(?P<ADDR>\w+):( *?(?P<VALUE>[a-f0-9]+))")

            try:
                match = re.search(pattern, gdb.execute(cmd, False, True))
                val = int(match.group("VALUE"), 16)
            except Exception as err:
                # if openocd can't access to addr => data abort
                return err
            return val
        else:
            return None

    def write(self, peripheral, register, val):
        """Write data to memory"""
        if register.access in [
            None,
            SVDAccessType.WRITE_ONLY,
            SVDAccessType.READ_WRITE,
            SVDAccessType.WRITE_ONCE,
            SVDAccessType.READ_WRITE_ONCE,
        ]:
            addr = peripheral.base_address + register.address_offset
            cmd = self.write_cmd.format(address=addr, value=val)

            gdb.execute(cmd, False, True)
        else:
            raise Exception("Register not writable")


# sub commands
class GdbSvdGetCmd(GdbSvdCmd):
    """Get register(s) value(s): svd get [peripheral] [register]"""

    def __init__(self, device, peripherals):
        GdbSvdCmd.__init__(self, device, peripherals)
        gdb.Command.__init__(self, "svd get", gdb.COMMAND_DATA)

    def complete(self, text, word):
        args = str(text).split(" ")
        if len(args) > 2:
            return gdb.COMPLETE_NONE

        return GdbSvdCmd.complete(self, text, word)

    def invoke(self, arg, from_tty):
        args = str(arg).split(" ")
        if len(args) > 2:
            gdb.write("Invalid parameter\n")
            gdb.execute("help svd get")
            return

        try:
            periph_name = args[0].upper()
            periph = self.peripherals[periph_name]
        except:
            gdb.write("Invalid peripheral name\n")
            GdbSvdCmd.print_desc_peripherals(self, self.device.peripherals)
            return

        try:
            regs = periph.registers

            if len(args) == 2:
                reg_name = args[1].upper()
                regs = [[r for r in regs if r.name == reg_name][0]]

            GdbSvdCmd.print_registers(self, periph, regs)

        except Exception as inst:
            gdb.write(f"{inst}\n")
        except:
            gdb.write("Error cannot get the value\n")


class GdbSvdSetCmd(GdbSvdCmd):
    """Set register value: svd set <peripheral> <register> [field] <value>"""

    def __init__(self, device, peripherals):
        GdbSvdCmd.__init__(self, device, peripherals)
        gdb.Command.__init__(self, "svd set", gdb.COMMAND_DATA)

    def complete(self, text, word):
        args = str(text).split(" ")
        if len(args) > 3:
            return gdb.COMPLETE_NONE

        return GdbSvdCmd.complete(self, text, word)

    def invoke(self, arg, from_tty):
        args = str(arg).split(" ")

        try:
            periph_name = args[0].upper()
            periph = self.peripherals[periph_name]
        except:
            gdb.write("Invalid peripheral name\n")
            GdbSvdCmd.print_desc_peripherals(self, self.device.peripherals)
            return

        if len(args) < 3 or len(args) > 4:
            gdb.write("Invalid parameter\n")
            gdb.execute("help svd set")
            return

        try:
            reg_name = args[1].upper()
            reg = [r for r in periph.registers if r.name == reg_name][0]
            field = None
            if len(args) == 4:
                field_name = args[2].upper()
                field = [f for f in reg.fields if f.name == field_name][0]
                value = int(args[3], 16)
            else:
                value = int(args[2], 16)

            GdbSvdCmd.set_register(self, periph, reg, value, field)

        except Exception as inst:
            gdb.write(f"{inst}\n")

        except:
            gdb.write("Error cannot set the value\n")


class GdbSvdInfoCmd(GdbSvdCmd):
    """Info on Peripheral|register|field: svd info <peripheral> [register] [field]"""

    def __init__(self, device, peripherals):
        GdbSvdCmd.__init__(self, device, peripherals)
        gdb.Command.__init__(self, "svd info", gdb.COMMAND_DATA)

    def complete(self, text, word):
        args = str(text).split(" ")
        if len(args) > 3:
            return gdb.COMPLETE_NONE

        return GdbSvdCmd.complete(self, text, word)

    def invoke(self, arg, from_tty):
        if arg == "":
            GdbSvdCmd.print_desc_peripherals(self, self.device.peripherals)
            return

        args = str(arg).split(" ")
        if not 1 <= len(args) <= 3:
            gdb.write(error("Invalid parameter\n"))
            gdb.execute("help svd info")
            return

        periph_name = args[0].upper()
        periphs = list(
            filter(lambda x: x.name.startswith(periph_name), self.device.peripherals)
        )

        if len(periphs) == 0:
            gdb.write(error(f"No peripheral with prefix '{periph_name}'\n"))
            GdbSvdCmd.print_desc_peripherals(self, self.device.peripherals)
            return

        if len(periphs) > 1:
            gdb.write(
                info(
                    f"Multiple peripherals with prefix '{periph_name}' found, please specify more!\n"
                )
            )
            GdbSvdCmd.print_desc_peripherals(self, periphs, periph_prefix=periph_name)
            return

        # this means we have only one peripheral
        periph = periphs[0]
        breadcrumbs = f"{periph.name}"

        if periph.name != periph_name:
            gdb.write(
                warning(
                    f"Only one peripheral with prefix '{periph_name}' found: {periph.name}\n"
                )
            )

        if len(args) == 1:
            GdbSvdCmd.print_desc_registers(self, breadcrumbs, periph.registers)
            return

        reg_name = args[1].upper()
        regs = list(filter(lambda x: x.name.startswith(reg_name), periph.registers))

        if len(regs) == 0:
            gdb.write(
                error(
                    f"No registers with prefix '{reg_name}' for peripheral '{periph.name}'\n"
                )
            )
            GdbSvdCmd.print_desc_registers(self, breadcrumbs, periph.registers)
            return

        if len(regs) > 1:
            gdb.write(
                info(
                    f"Multiple registers with prefix '{reg_name}' found for peripheral '{periph.name}, please specify more!\n"
                )
            )
            GdbSvdCmd.print_desc_registers(self, breadcrumbs, regs, reg_prefix=reg_name)
            return

        # this means we have only one register
        reg = regs[0]
        breadcrumbs += f":{reg.name}"

        if reg.name != reg_name:
            gdb.write(
                warning(
                    f"Only one register with prefix '{reg_name}' found: {reg.name}\n"
                )
            )

        if len(args) == 2:
            GdbSvdCmd.print_desc_fields(self, breadcrumbs, reg.fields)
            return

        field_name = args[2].upper()
        fields = list(filter(lambda x: x.name.startswith(field_name), reg.fields))

        if len(fields) == 0:
            gdb.write(
                error(
                    f"No fields found with prefix '{field_name}' in register '{reg.name}'\n"
                )
            )
            GdbSvdCmd.print_desc_fields(self, breadcrumbs, reg.fields)
            return

        GdbSvdCmd.print_desc_fields(self, breadcrumbs, fields, field_prefix=field_name)


class GdbSvdDumpCmd(GdbSvdCmd):
    """Get register(s) value(s): svd dump <filename> [peripheral]"""

    def __init__(self, device, peripherals):
        GdbSvdCmd.__init__(self, device, peripherals)
        gdb.Command.__init__(self, "svd dump", gdb.COMMAND_DATA)

    def complete(self, text, word):
        args = str(text).split(" ")
        nb_args = len(args)

        if nb_args == 1:
            return gdb.COMPLETE_FILENAME

        if nb_args > 2:
            return gdb.COMPLETE_NONE

        # remove first argument <filename>
        args.pop(0)

        return GdbSvdCmd.complete(self, " ".join(args), word)

    def invoke(self, arg, from_tty):
        args = str(arg).split(" ")
        if len(args) < 1 or len(args) > 2:
            gdb.write("Invalid parameter\n")
            gdb.execute("help svd dump")
            return
        try:
            output_file_name = args[0]
            gdb.write(f"Print to file: {output_file_name}\n")

            if len(args) >= 2:
                periph_name = args[1].upper()
                periphs = list(
                    filter(
                        lambda x: x.name.startswith(periph_name),
                        self.device.peripherals,
                    )
                )
                regs = None
            else:
                periphs = self.device.peripherals

            try:
                file_object = open(output_file_name, "w")
                file_object.write("Registers Dump\n")
                file_object.close()
                for per in periphs:
                    regs = per.registers
                    GdbSvdCmd.print_registers(self, per, regs, output_file_name, False)
            except:
                gdb.write(f"Error writting to file: {output_file_name}\n")
        except:
            gdb.write("Error cannot dump registers\n")
