# -*- coding: utf-8 -*-

"""A node or step for LAMMPS in a flowchart"""

import argparse
import configargparse
import cpuinfo
import glob
import lammps_step
import logging
from math import sqrt, exp, degrees, radians, cos, acos
# import numpy
import seamm
from seamm import data
import seamm_util
from seamm_util import ureg, Q_, units_class  # noqa: F401
import seamm_util.printing as printing
from seamm_util.printing import FormattedText as __
import os
import os.path
import pandas
import pprint
import statsmodels.tsa.stattools as stattools
import sys

from pymbar import timeseries

logger = logging.getLogger(__name__)
job = printing.getPrinter()
printer = printing.getPrinter('lammps')


def upcase(string):
    """Return an uppercase version of the string.

    Used for the type argument in argparse/
    """
    return string.upper()


bond_style = {
    'quadratic_bond': 'harmonic',
    'quartic_bond': 'class2',
    'fene': 'fene',
    'morse': 'morse',
}

angle_style = {
    'quadratic_angle': 'harmonic',
    'quartic_angle': 'class2',
}

dihedral_style = {
    'torsion_1': 'harmonic',
    'torsion_3': 'class2',
}

improper_style = {
    'wilson_out_of_plane': 'class2',
}

lammps_units = {
    'real':
        {
            '[mass]': 'g/mol',
            '[distance]': 'Å',
            '[time]': 'fs',
            '[length] ** 2 * [mass] / [substance] / [time] ** 2': 'kcal/mol',
            '[length] ** 2 * [mass] / [time] ** 2': 'kcal/mol',
            '[length] / [time]': 'Å/fs',
            '[length] * [mass] / [substance] / [time] ** 2': 'kcal/mol/Å',
            '[length] * [mass] / [time] ** 2': 'kcal/mol/Å',
            '[temperature]': 'K',
            '[mass] / [length] / [time] ** 2': 'bar',
            '[mass] / [length] / [time]': 'poise',
            '[current] * [time]': 'e',
            '[current] * [length] * [time]': 'e*Å',
            '[length] * [mass] / [current] / [time] ** 3': 'V/Å',
            '[mass] / [length] ** 3': 'g/mL'
        },
    'metal':
        {
            '[mass]': 'g/mol',
            '[distance]': 'Å',
            '[time]': 'ps',
            '[length] ** 2 * [mass] / [substance] / [time] ** 2': 'eV',
            '[length] ** 2 * [mass] / [time] ** 2': 'eV',
            '[length] / [time]': 'Å/ps',
            '[length] * [mass] / [substance] / [time] ** 2': 'eV/Å',
            '[length] * [mass] / [time] ** 2': 'eV/Å',
            '[temperature]': 'K',
            '[mass] / [length] / [time] ** 2': 'atm',
            '[mass] / [length] / [time]': 'poise',
            '[current] * [time]': 'e',
            '[current] * [length] * [time]': 'e*Å',
            '[length] * [mass] / [current] / [time] ** 3': 'V/Å',
            '[mass] / [length] ** 3': 'g/mL'
        }
}


class LAMMPS(seamm.Node):
    display_units = {
        "T": "K",
        "P": "atm",
        "t": "fs",
        "density": "g/mL",
        "a": "Å",
        "b": "Å",
        "c": "Å",
        "Etot": "kcal/mol",
        "Eke": "kcal/mol",
        "Epe": "kcal/mol",
        "Emol": "kcal/mol",
        "Epair": "kcal/mol",
    }
    display_title = {
        "T": "Temperature",
        "P": "Pressure",
        "t": "Time",
        "density": "Density",
        "a": "a lattice parameter",
        "b": "b lattice parameter",
        "c": "c lattice parameter",
        "Etot": "Total Energy",
        "Eke": "Kinetic Energy",
        "Epe": "Potential Energy",
        "Emol": "Molecular Energy, Valence Terms",
        "Epair": "Pair (Nonbond) Energy",
    }

    def __init__(
        self,
        flowchart=None,
        namespace='org.molssi.seamm.lammps',
        extension=None
    ):
        """Setup the main LAMMPS step

        Keyword arguments:
        """
        logger.debug('Creating LAMMPS {}'.format(self))

        # Argument/config parsing
        self.parser = configargparse.ArgParser(
            auto_env_var_prefix='',
            default_config_files=[
                '/etc/seamm/lammps.ini',
                '/etc/seamm/lammps_step.ini',
                '/etc/seamm/seamm.ini',
                '~/.seamm/lammps.ini',
                '~/.seamm/lammps_step.ini',
                '~/.seamm/seamm.ini',
            ]
        )

        self.parser.add_argument(
            '--seamm-configfile',
            is_config_file=True,
            default=None,
            help='a configuration file to override others'
        )

        # Options for this plugin
        self.parser.add_argument(
            "--lammps-log-level",
            default=argparse.SUPPRESS,
            choices=[
                'CRITICAL', 'ERROR', 'WARNING', 'INFO', 'DEBUG', 'NOTSET'
            ],
            type=upcase,
            help="the logging level for the LAMMPS step"
        )

        # General SEAMM options
        self.parser.add_argument(
            '--seamm-use-mpi',
            action='store_true',
            help='use mpi if this flag is present'
        )
        self.parser.add_argument(
            '--seamm-mpi-np',
            default=argparse.SUPPRESS,
            help='how many mpi processes to use'
        )
        self.parser.add_argument(
            '--seamm-mpi-max-np',
            default=argparse.SUPPRESS,
            help='maximum number of mpi processes to use'
        )
        self.parser.add_argument(
            '--seamm-mpiexec',
            default=argparse.SUPPRESS,
            help='the mpiexec command to use'
        )

        # LAMMPS specific options
        self.parser.add_argument(
            '--lammps-use-mpi',
            action='store_true',
            help='whether to use mpi for LAMMPS'
        )
        self.parser.add_argument(
            '--lammps-mpi-np',
            default=argparse.SUPPRESS,
            help='how many mpi processes to use for LAMMPS'
        )
        self.parser.add_argument(
            '--lammps-mpi-max-np',
            default=argparse.SUPPRESS,
            help='maximum number of mpi processes to use for LAMMPS'
        )
        self.parser.add_argument(
            '--lammps-mpiexec',
            default=argparse.SUPPRESS,
            help='the mpiexec command to use for LAMMPS'
        )
        self.parser.add_argument(
            '--lammps-serial',
            default='lmp_serial',
            help='the serial version of LAMMPS'
        )
        self.parser.add_argument(
            '--lammps-mpi',
            default='lmp_mpi',
            help='the mpi version of LAMMPS'
        )
        self.parser.add_argument(
            '--lammps-atoms-per-core',
            type=int,
            default='1000',
            help='the optimal number of atoms per core for LAMMPS'
        )
        self.parser.add_argument(
            '--lammps-html',
            action='store_true',
            help='whether to write out html files for graphs, etc.'
        )

        self.options, self.unknown = self.parser.parse_known_args()

        # Set the logging level for this module if requested
        if 'lammps_log_level' in self.options:
            logger.setLevel(self.options.lammps_log_level)

        # The subflowchart
        self.subflowchart = seamm.Flowchart(
            parent=self, name='LAMMPS', namespace=namespace
        )
        self.lammps_units = 'real'
        self._data = {}

        self.maxlags = 100

        super().__init__(
            flowchart=flowchart, title='LAMMPS', extension=extension
        )

    @property
    def version(self):
        """The semantic version of this module.
        """
        return lammps_step.__version__

    @property
    def git_revision(self):
        """The git version of this module.
        """
        return lammps_step.__git_revision__

    @staticmethod
    def box_to_cell(lx, ly, lz, xy, xz, yz):
        """Convert the LAMMPS box definition to cell parameters.
        """
        if xy == 0 and xz == 0 and yz == 0:
            a = lx
            b = ly
            c = lz
            alpha = 0.0
            beta = 0.0
            gamma = 0.0
        else:
            a = lx
            b = sqrt(ly**2 + xy**2)
            c = sqrt(lz**2 + xz**2 + yz**2)
            alpha = degrees(acos((xy * xz + lx * yz) / (b * c)))
            beta = degrees(acos(xz / c))
            gamma = degrees(acos(xy / b))

        return (a, b, c, alpha, beta, gamma)

    @staticmethod
    def cell_to_box(a, b, c, alpha, beta, gamma):
        """Convert cell parameters to the LAMMPS box."""
        if alpha == 90 and beta == 90 and gamma == 90:
            lx = a
            ly = b
            lz = c
            xy = xz = yz = 0.0
        else:
            lx = 0
            xy = b * cos(radians(gamma))
            xz = c * cos(radians(beta))
            ly = sqrt(b**2 - xy**2)
            yz = (b * c * cos(radians(alpha)) - xy * xz) / ly
            lz = sqrt(c**2 - xz**2 - yz**2)

        return (lx, ly, lz, xy, xz, yz)

    def set_id(self, node_id):
        """Set the id for node to a given tuple"""
        self._id = node_id

        # and set our subnodes
        self.subflowchart.set_ids(self._id)

        return self.next()

    def description_text(self, P=None):
        """Return a short description of this step.

        Return a nicely formatted string describing what this step will
        do.

        Keyword arguments:
            P: a dictionary of parameter values, which may be variables
                or final values. If None, then the parameters values will
                be used as is.
        """

        self.subflowchart.root_directory = self.flowchart.root_directory

        # Get the first real node
        node = self.subflowchart.get_node('1').next()

        text = self.header + '\n\n'
        while node is not None:
            try:
                text += __(node.description_text(), indent=3 * ' ').__str__()
            except Exception as e:
                print(
                    'Error describing LAMMPS flowchart: {} in {}'.format(
                        str(e), str(node)
                    )
                )
                logger.critical(
                    'Error describing LAMMPS flowchart: {} in {}'.format(
                        str(e), str(node)
                    )
                )
                raise
            except:  # noqa: E722
                print(
                    "Unexpected error describing LAMMPS flowchart: {} in {}"
                    .format(sys.exc_info()[0], str(node))
                )
                logger.critical(
                    "Unexpected error describing LAMMPS flowchart: {} in {}"
                    .format(sys.exc_info()[0], str(node))
                )
                raise
            text += '\n'
            node = node.next()

        return text

    def run(self):
        """Run a LAMMPS simulation
        """

        if data.structure is None:
            logger.error('LAMMPS run(): there is no structure!')
            raise RuntimeError('LAMMPS run(): there is no structure!')

        next_node = super().run(printer)

        # Parse the options
        o = self.options

        # Whether to run parallel and if so, how many mpi processes
        use_mpi = 'lammps_use_mpi' in o or 'seamm_use_mpi' in o
        if use_mpi:
            if 'seamm_mpi_np' in o:
                np = o.seamm_mpi_np
            elif 'lammps_mpi_np' in o:
                np = o.lammps_mpi_np
            else:
                np = 'default'

            if np == 'default':
                atoms = seamm.data.structure['atoms']
                n_atoms = len(atoms['elements'])
                np = int(round(n_atoms / o.lammps_atoms_per_core))
                if np < 1:
                    np = 1
            else:
                np = int(np)

            if np == 1:
                use_mpi = False
            else:
                if 'seamm_mpi_max_np' in o:
                    max_np = int(o.seamm_mpi_max_np)
                elif 'lammps_mpi_max_np' in o:
                    max_np = int(o.lammps_mpi_max_np)
                else:
                    max_np = 'default'

                if max_np == 'default':
                    # How many processors does this node have?
                    info = cpuinfo.get_cpu_info()
                    max_np = info['count']
                    # Account for Intel hyperthreading
                    if info['arch'][0:3] == 'X86':
                        max_np = int(max_np / 2)
                logger.info(
                    'The maximum number of cores to use is {}'.format(max_np)
                )
                if np > max_np:
                    np = max_np

        if use_mpi:
            if 'lammps_mpiexec' in o:
                mpiexec = o.lammps_mpiexec
            elif 'seamm_mpiexec' in o:
                mpiexec = o.seamm_mpiexec
            else:
                use_mpi = False

        # Print headers and get to work
        printer.important(self.header)
        if use_mpi:
            printer.important(
                '    LAMMPS using MPI with {} processes.\n'.format(np)
            )
        else:
            printer.important('   LAMMPS using the serial version.\n')

        logger.info('\n' + 80 * '-' + '\n' + self.parser.format_help())
        logger.info('\n' + 80 * '-' + '\n' + self.parser.format_values())

        self.subflowchart.root_directory = self.flowchart.root_directory

        # Get the first real node
        node = self.subflowchart.get_node('1').next()

        input_data = []
        extras = {}

        history_nodes = []

        # Create overall directory for the lammps step

        os.makedirs(self.directory, exist_ok=True)


        while node is not None: 

            if isinstance(node, lammps_step.Initialization): 
                try:
                    lines, eex = node.get_input()
                except Exception as e:
                    print(
                        'Error running LAMMPS flowchart: {} in {}'.format(
                            str(e), str(node)
                        )
                    )
                    logger.critical(
                        'Error running LAMMPS flowchart: {} in {}'.format(
                            str(e), str(node)
                        )
                    )
                    raise
                except:  # noqa: E722
                    print(
                        "Unexpected error running LAMMPS flowchart: {} in {}"
                        .format(sys.exc_info()[0], str(node))
                    )
                    logger.critical(
                        "Unexpected error running LAMMPS flowchart: {} in {}"
                        .format(sys.exc_info()[0], str(node))
                    )
                    raise

                files = {}
                files['structure.dat'] = '\n'.join(self.structure_data(eex))
                with open(os.path.join(self.directory,'structure.dat'), mode='w') as fd:
                    fd.write(files['structure.dat'])
                logger.debug('structure.dat:\n' + files['structure.dat'])
                initialization_header = lines
                input_data += lines
                # Find the bond & angle types as needed for shake/rattle
                P = node.parameters.current_values_to_dict(
                    context=seamm.flowchart_variables._data
                )
                shake = self.shake_fix(P, eex)
                if shake != '':
                    extras['shake'] = shake

                history_nodes.append(node)

            else:



                try:
                    new_input_data = node.get_input(extras)
                except Exception as e:
                    print(
                        'Error running LAMMPS flowchart: {} in {}'.format(
                            str(e), str(node)
                        )
                    )
                    logger.critical(
                        'Error running LAMMPS flowchart: {} in {}'.format(
                            str(e), str(node)
                        )
                    )
                    raise
                except:  # noqa: E722
                    print(
                        "Unexpected error running LAMMPS flowchart: {} in {}"
                        .format(sys.exc_info()[0], str(node))
                    )
                    logger.critical(
                        "Unexpected error running LAMMPS flowchart: {} in {}"
                        .format(sys.exc_info()[0], str(node))
                    )
                    raise


                P = node.parameters.current_values_to_dict(
                    context=seamm.flowchart_variables._data
                )


                if 'run_control' in P:
                    if 'Until properties converge' in P['run_control']:

                        history_nodes_ids = [n._id[1] for n in history_nodes]
                        accum_base = 'lammps_substep_%s_iter_0' % ('_'.join(history_nodes_ids)) 
                        accum_infile = accum_base + '.dat' 
                        accum_dump = accum_base + '.dump.*' 
                        input_data.append('write_dump          all custom  %s id xu yu zu modify flush yes sort id' % (accum_dump))

                        files[accum_infile] = '\n'.join(input_data)

                        logger.debug(accum_infile + ':\n' + files[accum_infile])

                        # Get the structure file from the eex
                        
                        with open(os.path.join(self.directory, accum_infile), mode='w') as fd:
                            fd.write(files[accum_infile])

                        return_files = ['summary_*.txt', 'trajectory_*.seamm_trj', '*.dump.*']
                        local = seamm.ExecLocal()
                        
                        if use_mpi:
                            cmd = [mpiexec, '-np', str(np), o.lammps_mpi, '-in', accum_infile]
                        else:
                            cmd = [o.lammps_serial, '-in', accum_infile]
                        
                        result = local.run(cmd=cmd, files=files, return_files=return_files)
                        
                        if result is None:
                            logger.error('There was an error running LAMMPS')
                            return None
                        
                        logger.debug('\n' + pprint.pformat(result))
                        
                        logger.debug('stdout:\n' + result['stdout'])
                        
                        with open(os.path.join(self.directory, 'stdout.txt'), mode='w') as fd:
                            fd.write(result['stdout'])
                        
                        if result['stderr'] != '':
                            logger.warning('stderr:\n' + result['stderr'])
                            with open(
                                os.path.join(self.directory, 'stderr.txt'), mode='w'
                            ) as fd:
                                fd.write(result['stderr'])

                        for filename in result['files']:
                            with open(os.path.join(self.directory, filename), mode='w') as fd:
                                if result[filename]['data'] is not None:
                                    fd.write(result[filename]['data'])
                                else:
                                    fd.write(result[filename]['exception'])

#                        import pdb
#                        pdb.set_trace()
#
#                        try: 
#                                prev_node = node.previous()
#
#                                P_prev = prev_node.parameters.to_dict()
#
#                                last_snapshot = str(P_prev['time'].magnitude)
                                
#                        except KeyError: # Previous step was NVT or NPT or was a complex step

                                # Need to figure out for how long the previous step ran. 

                        accum_dump_filenames= glob.glob(os.path.join(self.directory, accum_dump))

                         # Probably the step didn't run
                        if len(accum_dump_filenames) == 0:
                            raise FileNotFoundError('Lammps_step: could not find any file with the pattern %s' % (accum_dump))

                        run_lengths = []

                        for accum_dump in accum_dump_filenames:
                            try:
                                pre, ext = os.path.splitext(accum_dump) 
                                ext = int(ext.strip('.'))
                            except ValueError:
                                raise Exception('Lammps_step: could not extract run length from dump file %s' % (accum_dump))
                            run_lengths.append(ext)
                               
                            last_snapshot = str(max(run_lengths))

                        accum_dump = accum_dump.replace('*', last_snapshot)
                        self.read_dump(os.path.join(self.directory, accum_dump))
                                    
                        # Update the coordinates in the system

                        #self.analyze()
                        print('Analyzing steps ', ' '.join(history_nodes_ids))

                        iteration = 0

                        if P['timestep'] == 'normal':
                            timestep = 1.0
                        else:
                            timestep = P['timestep'].to('fs').magnitude

                        while True:


                            P = node.parameters.current_values_to_dict(
                                context=seamm.flowchart_variables._data
                            )

                            new_input_data.insert(0, 'read_dump          %s %s x y z' % (accum_dump, last_snapshot))
                            curr_base = 'lammps_substep_%s_iter_%d' % (node._id[1], iteration) 

                            time = P['time'].to('fs').magnitude

                            nsteps = round(time / timestep)

                            curr_dump = curr_base + '.dump.%d' % nsteps #  + node.parameters['time']['value'] 

                            new_input_data = initialization_header + new_input_data
                            new_input_data.append('write_dump          all custom  %s id xu yu zu modify flush yes sort id' % (curr_dump))

                            # Create input file for the current substep iteration
   
                            curr_infile = curr_base + '.dat'
    
                            files[curr_infile] = '\n'.join(new_input_data)

                            with open(os.path.join(self.directory, accum_dump), 'r') as fd:
                                files[accum_dump] = fd.read()
    
                            logger.debug(curr_infile + ':\n' + files[curr_infile])
    
                            # Get the structure file from the eex
                            
                            with open(os.path.join(self.directory, curr_infile), mode='w') as fd:
                                fd.write(files[curr_infile])
    
                            return_files = ['summary_*.txt', 'trajectory_*.seamm_trj', '*.dump.*']

                            local = seamm.ExecLocal()
                            
                            if use_mpi:
                                cmd = [mpiexec, '-np', str(np), o.lammps_mpi, '-in', curr_infile]
                            else:
                                cmd = [o.lammps_serial, '-in', curr_infile]
                            
                            result = local.run(cmd=cmd, files=files, return_files=return_files)
                            
                            if result is None:
                                logger.error('There was an error running LAMMPS')
                                return None
                            
                            logger.debug('\n' + pprint.pformat(result))
                            
                            logger.debug('stdout:\n' + result['stdout'])
                            
                            with open(os.path.join(self.directory, 'stdout.txt'), mode='w') as fd:
                                fd.write(result['stdout'])
                            
                            if result['stderr'] != '':
                                logger.warning('stderr:\n' + result['stderr'])
                                with open(
                                    os.path.join(self.directory, 'stderr.txt'), mode='w'
                                ) as fd:
                                    fd.write(result['stderr'])
                            
                            for filename in result['files']:
                                with open(os.path.join(self.directory, filename), mode='w') as fd:
                                    if result[filename]['data'] is not None:
                                        fd.write(result[filename]['data'])
                                    else:
                                        fd.write(result[filename]['exception'])
                                
                            # Update the coordinates in the system
                            self.read_dump(os.path.join(self.directory, curr_dump))

                            accum_dump = curr_dump
                            last_snapshot = os.path.splitext(accum_dump)[1].strip('.')
                                    

                            try:
                                new_input_data = node.get_input(extras)
                            except Exception as e:
                                print(
                                    'Error running LAMMPS flowchart: {} in {}'.format(
                                        str(e), str(node)
                                    )
                                )
                                logger.critical(
                                    'Error running LAMMPS flowchart: {} in {}'.format(
                                        str(e), str(node)
                                    )
                                )
                                raise
                            except:  # noqa: E722
                                print(
                                    "Unexpected error running LAMMPS flowchart: {} in {}"
                                    .format(sys.exc_info()[0], str(node))
                                )
                                logger.critical(
                                    "Unexpected error running LAMMPS flowchart: {} in {}"
                                    .format(sys.exc_info()[0], str(node))
                                )
                                raise

                            # Analyze the results
                            analysis = self.analyze(node=node)

                            if analysis['Epe,short_production_warning'] is False:
                                if analysis['Epe,few_neff_warning'] is False:

                                    history_nodes = []
                                    input_data = initialization_header
                                    break

                            for idx, line in enumerate(new_input_data):
                                if 'run' in line:
                                    new_line = new_input_data[idx].split()
                                    new_nsteps = int(new_line[1]) * 2
                                    new_line[1] = str(new_nsteps)
                                    new_line = '              '.join(new_line)
                                    new_input_data[idx] = new_line
                            new_time = new_nsteps * timestep * ureg.femtosecond 
                            new_time = new_time.to(P['time'].units)

                            node.parameters['time'].value = new_time.magnitude
                            #P_temp = node.parameters.current_values_to_dict(
                            #    context=seamm.flowchart_variables._data
                            #)

                            #P_temp['time'] = new_time

                            #node.parameters.update(P_temp)
                            
                            iteration = iteration + 1

#                            if analysis['Epe,short_production_warning']: 
#                                for idx, line in enumerate(new_input_data):
#                                    if 'run' in line:
#                                        new_line = new_input_data[idx].split()
#                                        new_nsteps = int(new_line[1]) * 2
#                                        new_line[1] = str(new_nsteps)
#                                        new_line = '              '.join(new_line)
#                                        new_input_data[idx] = new_line
#                            else:
#                                if analysis['Epe,few_neff_warning']:
#                                    for idx, line in enumerate(new_input_data):
#                                        if 'run' in line:
#                                            new_line = new_input_data[idx].split()
#                                            new_nsteps = int(new_line[1]) * 2
#                                            new_line[1] = str(new_nsteps)
#                                            new_line = '              '.join(new_line)
#                                            new_input_data[idx] = new_line
#
#                                else:
#                                    history_nodes = []
#                                    input_data = initialization_header
#                                    break





#                            else:
#                                import pdb
#                                pdb.set_trace()
#                                print(results)

                            #iteration = iteration + 1

                            #if iteration > 2:
                            #    history_nodes = []

                            #    input_data = initialization_header
                            #    break


                    else:

                        input_data += new_input_data
                        history_nodes.append(node)

                else:
                    try:
                        new_input_data = node.get_input(extras)
                    except Exception as e:
                        print(
                            'Error running LAMMPS flowchart: {} in {}'.format(
                                str(e), str(node)
                            )
                        )
                        logger.critical(
                            'Error running LAMMPS flowchart: {} in {}'.format(
                                str(e), str(node)
                            )
                        )
                        raise
                    except:  # noqa: E722
                        print(
                            "Unexpected error running LAMMPS flowchart: {} in {}"
                            .format(sys.exc_info()[0], str(node))
                        )
                        logger.critical(
                            "Unexpected error running LAMMPS flowchart: {} in {}"
                            .format(sys.exc_info()[0], str(node))
                        )
                        raise

                    input_data += new_input_data
                    history_nodes.append(node)

            node = node.next()



        if len(history_nodes) > 0:

            history_nodes_ids = [n._id[1] for n in history_nodes]
            accum_base = 'lammps_substep_%s_iter_0' % ('_'.join(history_nodes_ids)) 
            accum_infile = accum_base + '.dat' 
            accum_dump = accum_base + '.dump.*' 
            input_data.append('write_dump          all custom  %s id xu yu zu modify flush yes sort id' % (accum_dump))
            
            files[accum_infile] = '\n'.join(input_data)
            
            logger.debug(accum_infile + ':\n' + files[accum_infile])
            
            # Get the structure file from the eex
            
            with open(os.path.join(self.directory, accum_infile), mode='w') as fd:
                fd.write(files[accum_infile])
            
            return_files = ['summary_*.txt', 'trajectory_*.seamm_trj', '*.dump.*']
            local = seamm.ExecLocal()
            
            if use_mpi:
                cmd = [mpiexec, '-np', str(np), o.lammps_mpi, '-in', accum_infile]
            else:
                cmd = [o.lammps_serial, '-in', accum_infile]
            
            result = local.run(cmd=cmd, files=files, return_files=return_files)
            
            if result is None:
                logger.error('There was an error running LAMMPS')
                return None
            
            logger.debug('\n' + pprint.pformat(result))
            
            logger.debug('stdout:\n' + result['stdout'])
            
            with open(os.path.join(self.directory, 'stdout.txt'), mode='w') as fd:
                fd.write(result['stdout'])
            
            if result['stderr'] != '':
                logger.warning('stderr:\n' + result['stderr'])
                with open(
                    os.path.join(self.directory, 'stderr.txt'), mode='w'
                ) as fd:
                    fd.write(result['stderr'])
            
            for filename in result['files']:
                with open(os.path.join(self.directory, filename), mode='w') as fd:
                    if result[filename]['data'] is not None:
                        fd.write(result[filename]['data'])
                    else:
                        fd.write(result[filename]['exception'])
            
           
            accum_dump_filenames= glob.glob(os.path.join(self.directory, accum_dump))
     
             # Probably the step didn't run
            if len(accum_dump_filenames) == 0:
                raise FileNotFoundError('Lammps_step: could not find any file with the pattern %s' % (accum_dump))
     
            run_lengths = []
     
            for accum_dump in accum_dump_filenames:
                try:
                    pre, ext = os.path.splitext(accum_dump) 
                    ext = int(ext.strip('.'))
                except ValueError:
                    raise Exception('Lammps_step: could not extract run length from dump file %s' % (accum_dump))
                run_lengths.append(ext)
                   
                last_snapshot = str(max(run_lengths))
     
            accum_dump = accum_dump.replace('*', last_snapshot)
     
            # Update the coordinates in the system
            self.read_dump(os.path.join(self.directory, accum_dump))
                        
           
            for past_node in history_nodes:
                self.analyze(node=past_node)

        return next_node




    def structure_data(self, eex, triclinic=False):
        """Create the LAMMPS structure file from the energy expression"""
        lines = []
        lines.append(
            'Structure file for LAMMPS generated by a MolSSI flowchart'
        )
        lines.append('{:10d} atoms'.format(eex['n_atoms']))
        lines.append('{:10d} atom types'.format(eex['n_atom_types']))
        if 'n_bonds' in eex and eex['n_bonds'] > 0:
            lines.append('{:10d} bonds'.format(eex['n_bonds']))
            lines.append('{:10d} bond types'.format(eex['n_bond_types']))
        if 'n_angles' in eex and eex['n_angles'] > 0:
            lines.append('{:10d} angles'.format(eex['n_angles']))
            lines.append('{:10d} angle types'.format(eex['n_angle_types']))
        if 'n_torsions' in eex and eex['n_torsions'] > 0:
            lines.append('{:10d} dihedrals'.format(eex['n_torsions']))
            lines.append(
                '{:10d} dihedral types'.format(eex['n_torsion_types'])
            )
        if 'n_oops' in eex and eex['n_oops'] > 0:
            lines.append('{:10d} impropers'.format(eex['n_oops']))
            lines.append('{:10d} improper types'.format(eex['n_oop_types']))

        # Find the box limits
        periodicity = eex['periodicity']
        if periodicity == 3:
            a, b, c, alpha, beta, gamma = eex['cell']
            lx, ly, lz, xy, xz, yz = LAMMPS.cell_to_box(
                a, b, c, alpha, beta, gamma
            )

            lines.append('{} {} xlo xhi'.format(0.0, lx))
            lines.append('{} {} ylo yhi'.format(0.0, ly))
            lines.append('{} {} zlo zhi'.format(0.0, lz))

            xy = xy if abs(xy) > 1.0e-06 else 0.0
            xz = xz if abs(xy) > 1.0e-06 else 0.0
            yz = yz if abs(xy) > 1.0e-06 else 0.0

            if triclinic or xy > 0.0 or xz > 0.0 or yz > 0.0:
                lines.append('{} {} {} xy xz yz'.format(xy, xz, yz))
        else:
            x, y, z, index = eex['atoms'][0]
            xlo = xhi = x
            ylo = yhi = y
            zlo = zhi = z
            for x, y, z, index in eex['atoms']:
                xlo = x if x < xlo else xlo
                xhi = x if x > xhi else xlo
                ylo = y if y < ylo else ylo
                yhi = y if y > yhi else ylo
                zlo = z if z < zlo else zlo
                zhi = z if z > zhi else zlo

            # Some extra space....
            xlo -= 10.0
            xhi += 10.0
            ylo -= 10.0
            yhi += 10.0
            zlo -= 10.0
            zhi += 10.0

            lines.append('{} {} xlo xhi'.format(xlo, xhi))
            lines.append('{} {} ylo yhi'.format(ylo, yhi))
            lines.append('{} {} zlo zhi'.format(zlo, zhi))

        # the atoms and their masses, etc.
        lines.append('')
        lines.append('Atoms')
        lines.append('')

        for i, xyz_index, q in zip(
            range(1, eex['n_atoms'] + 1), eex['atoms'], eex['charges']
        ):
            x, y, z, index = xyz_index
            lines.append(
                '{:6d} {:6d} {:6d} {:6.3f} {:12.7f} {:12.7f} {:12.7f}'.format(
                    i, 1, index, q, x, y, z
                )
            )
        lines.append('')

        lines.append('Masses')
        lines.append('')
        for i, parameters in zip(
            range(1, eex['n_atom_types'] + 1), eex['masses']
        ):
            mass, itype = parameters
            lines.append('{:6d} {} # {}'.format(i, mass, itype))

        # nonbonds
        lines.append('')
        lines.append('Pair Coeffs')
        lines.append('')
        for i, parameters in zip(
            range(1, eex['n_atom_types'] + 1), eex['nonbond parameters']
        ):
            form, values, types, parameters_type, real_types = \
                parameters
            if form == 'nonbond(9-6)':
                lines.append(
                    '{:6d} {} {} # {} --> {}'.format(
                        i, values['eps'], values['rmin'], types[0],
                        real_types[0]
                    )
                )
            else:
                lines.append(
                    '{:6d} {} {} # {} --> {}'.format(
                        i, values['eps'], values['sigma'], types[0],
                        real_types[0]
                    )
                )

        # bonds
        if 'n_bonds' in eex and eex['n_bonds'] > 0:
            lines.append('')
            lines.append('Bonds')
            lines.append('')
            for counter, tmp in zip(
                range(1, eex['n_bonds'] + 1), eex['bonds']
            ):
                i, j, index = tmp
                lines.append(
                    '{:6d} {:6d} {:6d} {:6d}'.format(counter, index, i, j)
                )

            lines.append('')
            lines.append('Bond Coeffs')
            lines.append('')
            for counter, parameters in zip(
                range(1, eex['n_bond_types'] + 1), eex['bond parameters']
            ):
                form, values, types, parameters_type, real_types = \
                    parameters
                if form == 'quadratic_bond':
                    # '{:6d} harmonic {} {}'
                    lines.append(
                        '{:6d} {} {}'
                        .format(counter, values['K2'], values['R0']) +
                        ' # {}-{} --> {}-{}'.format(
                            types[0], types[1], real_types[0], real_types[1]
                        )
                    )
                elif form == 'quartic_bond':
                    lines.append(
                        '{:6d} class2 {} {} {} {}'.format(
                            counter, values['R0'], values['K2'], values['K3'],
                            values['K4']
                        ) + ' # {}-{} --> {}-{}'.format(
                            types[0], types[1], real_types[0], real_types[1]
                        )
                    )

        # angles
        if 'n_angles' in eex and eex['n_angles'] > 0:
            lines.append('')
            lines.append('Angles')
            lines.append('')
            for counter, tmp in zip(
                range(1, eex['n_angles'] + 1), eex['angles']
            ):
                i, j, k, index = tmp
                lines.append(
                    '{:6d} {:6d} {:6d} {:6d} {:6d}'.format(
                        counter, index, i, j, k
                    )
                )

            lines.append('')
            lines.append('Angle Coeffs')
            lines.append('')
            for counter, parameters in zip(
                range(1, eex['n_angle_types'] + 1), eex['angle parameters']
            ):
                form, values, types, parameters_type, real_types = \
                    parameters
                if form == 'quadratic_angle':
                    # '{:6d} harmonic {} {}'
                    lines.append(
                        '{:6d} {} {}'
                        .format(counter, values['K2'], values['Theta0']) +
                        ' # {}-{}-{} --> {}-{}-{}'.format(
                            types[0], types[1], types[2], real_types[0],
                            real_types[1], real_types[2]
                        )
                    )
                elif form == 'quartic_angle':
                    lines.append(
                        '{:6d} class2 {} {} {} {}'.format(
                            counter, values['Theta0'], values['K2'],
                            values['K3'], values['K4']
                        ) + ' # {}-{}-{} --> {}-{}-{}'.format(
                            types[0], types[1], types[2], real_types[0],
                            real_types[1], real_types[2]
                        )
                    )

            # bond-bond coefficients, which must match angles in order & number
            if 'n_bond-bond_types' in eex:
                lines.append('')
                lines.append('BondBond Coeffs')
                lines.append('')
                for counter, parameters, angles in zip(
                    range(1, eex['n_bond-bond_types'] + 1),
                    eex['bond-bond parameters'], eex['angle parameters']
                ):
                    form, values, types, parameters_type, real_types = \
                        parameters
                    angle_form = angles[0]
                    if angle_form == 'quartic_angle':
                        lines.append(
                            '{:6d} class2 {} {} {}'.format(
                                counter, values['K'], values['R10'],
                                values['R20']
                            ) + ' # {}-{}-{} --> {}-{}-{}'.format(
                                types[0], types[1], types[2], real_types[0],
                                real_types[1], real_types[2]
                            )
                        )
                    else:
                        lines.append(
                            '{:6d} skip'.format(counter) +
                            ' # {}-{}-{} --> {}-{}-{}'.format(
                                types[0], types[1], types[2], real_types[0],
                                real_types[1], real_types[2]
                            )
                        )

                # bond-angles coefficients, which must match angles in order &
                # number
                lines.append('')
                lines.append('BondAngle Coeffs')
                lines.append('')
                for counter, parameters, angles in zip(
                    range(1, eex['n_bond-angle_types'] + 1),
                    eex['bond-angle parameters'], eex['angle parameters']
                ):
                    form, values, types, parameters_type, real_types = \
                        parameters
                    angle_form = angles[0]
                    if angle_form == 'quartic_angle':
                        lines.append(
                            '{:6d} class2 {} {} {} {}'.format(
                                counter, values['K12'], values['K23'],
                                values['R10'], values['R20']
                            ) + ' # {}-{}-{} --> {}-{}-{}'.format(
                                types[0], types[1], types[2], real_types[0],
                                real_types[1], real_types[2]
                            )
                        )
                    else:
                        lines.append(
                            '{:6d} skip'.format(counter) +
                            ' # {}-{}-{} --> {}-{}-{}'.format(
                                types[0], types[1], types[2], real_types[0],
                                real_types[1], real_types[2]
                            )
                        )

        # torsions
        if 'n_torsions' in eex and eex['n_torsions'] > 0:
            lines.append('')
            lines.append('Dihedrals')
            lines.append('')
            for counter, tmp in zip(
                range(1, eex['n_torsions'] + 1), eex['torsions']
            ):
                i, j, k, l, index = tmp
                lines.append(
                    '{:6d} {:6d} {:6d} {:6d} {:6d} {:6d}'.format(
                        counter, index, i, j, k, l
                    )
                )

            lines.append('')
            lines.append('Dihedral Coeffs')
            lines.append('')
            for counter, parameters in zip(
                range(1, eex['n_torsion_types'] + 1), eex['torsion parameters']
            ):
                form, values, types, parameters_type, real_types = \
                    parameters
                if form == 'torsion_1':
                    KPhi = values['KPhi']
                    n = values['n']
                    Phi0 = values['Phi0']

                    # Discover form is
                    #  KPhi * [1 + cos(n*Phi - Phi0)]
                    #  with trans = 180
                    #
                    #  For ethane, Phi0 = 0 so at Phi=180 E is min. Correct

                    # Lammps for is
                    #  KPhi * [1 + d*cos(n*Phi)]
                    #  with trans = 180
                    #
                    # Again for ethane, d=+1 and at Phi=180, E is min.
                    #
                    # Phi0 = 0   ==> d=+1
                    # Phi0 = 180 ==> d=-1

                    if float(Phi0) == 0.0:
                        d = '-1'
                    elif float(Phi0) == 180.0:
                        d = '+1'
                    else:
                        raise RuntimeError(
                            'LAMMPS cannot handle Phi0 = {}'.format(Phi0)
                        )

                    lines.append(
                        '{:6d} harmonic {} {} {}'.format(counter, KPhi, d, n) +
                        ' # {}-{}-{}-{} --> {}-{}-{}-{}'.format(
                            types[0], types[1], types[2], types[3],
                            real_types[0], real_types[1], real_types[2],
                            real_types[3]
                        )
                    )
                elif form == 'torsion_3':
                    lines.append(
                        '{:6d} class2 {} {} {} {} {} {}'.format(
                            counter, values['V1'], values['Phi0_1'],
                            values['V2'], values['Phi0_2'], values['V3'],
                            values['Phi0_3']
                        ) + ' # {}-{}-{}-{} --> {}-{}-{}-{}'.format(
                            types[0], types[1], types[2], types[3],
                            real_types[0], real_types[1], real_types[2],
                            real_types[3]
                        )
                    )

            # middle bond-torsion_3 coefficients, which must match torsions
            # in order & number
            if 'n_middle_bond-torsion_3_types' in eex:
                lines.append('')
                lines.append('MiddleBondTorsion Coeffs')
                lines.append('')
                for counter, parameters, torsions in zip(
                    range(1, eex['n_middle_bond-torsion_3_types'] + 1),
                    eex['middle_bond-torsion_3 parameters'],
                    eex['torsion parameters']
                ):
                    form, values, types, parameters_type, real_types = \
                        parameters
                    torsion_form = torsions[0]
                    if torsion_form == 'torsion_3':
                        lines.append(
                            '{:6d} class2 {} {} {} {}'.format(
                                counter, values['V1'], values['V2'],
                                values['V3'], values['R0']
                            ) + ' # {}-{}-{}-{} --> {}-{}-{}-{}'.format(
                                types[0], types[1], types[2], types[3],
                                real_types[0], real_types[1], real_types[2],
                                real_types[3]
                            )
                        )
                    else:
                        lines.append(
                            '{:6d} skip'.format(counter) +
                            ' # {}-{}-{}-{} --> {}-{}-{}-{}'.format(
                                types[0], types[1], types[2], types[3],
                                real_types[0], real_types[1], real_types[2],
                                real_types[3]
                            )
                        )

                # end bond-torsion_3 coefficients, which must match torsions
                # in order & number
                lines.append('')
                lines.append('EndBondTorsion Coeffs')
                lines.append('')
                for counter, parameters, torsions in zip(
                    range(1, eex['n_end_bond-torsion_3_types'] + 1),
                    eex['end_bond-torsion_3 parameters'],
                    eex['torsion parameters']
                ):
                    form, values, types, parameters_type, real_types = \
                        parameters
                    torsion_form = torsions[0]
                    if torsion_form == 'torsion_3':
                        lines.append(
                            '{:6d} class2 {} {} {} {} {} {} {} {}'.format(
                                counter, values['V1_L'], values['V2_L'],
                                values['V3_L'], values['V1_R'], values['V2_R'],
                                values['V3_R'], values['R0_L'], values['R0_R']
                            ) + ' # {}-{}-{}-{} --> {}-{}-{}-{}'.format(
                                types[0], types[1], types[2], types[3],
                                real_types[0], real_types[1], real_types[2],
                                real_types[3]
                            )
                        )
                    else:
                        lines.append(
                            '{:6d} skip'.format(counter) +
                            ' # {}-{}-{}-{} --> {}-{}-{}-{}'.format(
                                types[0], types[1], types[2], types[3],
                                real_types[0], real_types[1], real_types[2],
                                real_types[3]
                            )
                        )

                # angle-torsion_3 coefficients, which must match torsions
                # in order & number
                lines.append('')
                lines.append('AngleTorsion Coeffs')
                lines.append('')
                for counter, parameters, torsions in zip(
                    range(1, eex['n_angle-torsion_3_types'] + 1),
                    eex['angle-torsion_3 parameters'],
                    eex['torsion parameters']
                ):
                    form, values, types, parameters_type, real_types = \
                        parameters
                    torsion_form = torsions[0]
                    if torsion_form == 'torsion_3':
                        lines.append(
                            '{:6d} class2 {} {} {} {} {} {} {} {}'.format(
                                counter, values['V1_L'], values['V2_L'],
                                values['V3_L'], values['V1_R'], values['V2_R'],
                                values['V3_R'], values['Theta0_L'],
                                values['Theta0_R']
                            ) + ' # {}-{}-{}-{} --> {}-{}-{}-{}'.format(
                                types[0], types[1], types[2], types[3],
                                real_types[0], real_types[1], real_types[2],
                                real_types[3]
                            )
                        )
                    else:
                        lines.append(
                            '{:6d} skip'.format(counter) +
                            ' # {}-{}-{}-{} --> {}-{}-{}-{}'.format(
                                types[0], types[1], types[2], types[3],
                                real_types[0], real_types[1], real_types[2],
                                real_types[3]
                            )
                        )

                # angle-angle-torsion_1 coefficients, which must match torsions
                # in order & number
                lines.append('')
                lines.append('AngleAngleTorsion Coeffs')
                lines.append('')
                for counter, parameters, torsions in zip(
                    range(1, eex['n_angle-angle-torsion_1_types'] + 1),
                    eex['angle-angle-torsion_1 parameters'],
                    eex['torsion parameters']
                ):
                    form, values, types, parameters_type, real_types = \
                        parameters
                    torsion_form = torsions[0]
                    if torsion_form == 'torsion_3':
                        lines.append(
                            '{:6d} class2 {} {} {}'.format(
                                counter, values['K'], values['Theta0_L'],
                                values['Theta0_R']
                            ) + ' # {}-{}-{}-{} --> {}-{}-{}-{}'.format(
                                types[0], types[1], types[2], types[3],
                                real_types[0], real_types[1], real_types[2],
                                real_types[3]
                            )
                        )
                    else:
                        lines.append(
                            '{:6d} skip'.format(counter) +
                            ' # {}-{}-{}-{} --> {}-{}-{}-{}'.format(
                                types[0], types[1], types[2], types[3],
                                real_types[0], real_types[1], real_types[2],
                                real_types[3]
                            )
                        )

                # bond-bond_1_3 coefficients, which must match torsions
                # in order & number
                lines.append('')
                lines.append('BondBond13 Coeffs')
                lines.append('')
                for counter, parameters, torsions in zip(
                    range(1, eex['n_bond-bond_1_3_types'] + 1),
                    eex['bond-bond_1_3 parameters'], eex['torsion parameters']
                ):
                    form, values, types, parameters_type, real_types = \
                        parameters
                    torsion_form = torsions[0]
                    if torsion_form == 'torsion_3':
                        lines.append(
                            '{:6d} class2 {} {} {}'.format(
                                counter, values['K'], values['R10'],
                                values['R30']
                            ) + ' # {}-{}-{}-{} --> {}-{}-{}-{}'.format(
                                types[0], types[1], types[2], types[3],
                                real_types[0], real_types[1], real_types[2],
                                real_types[3]
                            )
                        )
                    else:
                        lines.append(
                            '{:6d} skip'.format(counter) +
                            ' # {}-{}-{}-{} --> {}-{}-{}-{}'.format(
                                types[0], types[1], types[2], types[3],
                                real_types[0], real_types[1], real_types[2],
                                real_types[3]
                            )
                        )

        # out-of-planes
        if 'n_oops' in eex and eex['n_oops'] > 0:
            lines.append('')
            lines.append('Impropers')
            lines.append('')
            for counter, tmp in zip(range(1, eex['n_oops'] + 1), eex['oops']):
                i, j, k, l, index = tmp
                lines.append(
                    '{:6d} {:6d} {:6d} {:6d} {:6d} {:6d}'.format(
                        counter, index, i, j, k, l
                    )
                )

            lines.append('')
            lines.append('Improper Coeffs')
            lines.append('')
            for counter, parameters in zip(
                range(1, eex['n_oop_types'] + 1), eex['oop parameters']
            ):
                form, values, types, parameters_type, real_types = \
                    parameters
                lines.append(
                    '{:6d} {} {}'.format(counter, values['K'], values['Chi0'])
                    + ' # {}-{}-{}-{} --> {}-{}-{}-{}'.format(
                        types[0], types[1], types[2], types[3], real_types[0],
                        real_types[1], real_types[2], real_types[3]
                    )
                )

            # angle-angle
            if 'n_angle-angle_types' in eex:
                lines.append('')
                lines.append('AngleAngle Coeffs')
                lines.append('')
                for counter, parameters in zip(
                    range(1, eex['n_angle-angle_types'] + 1),
                    eex['angle-angle parameters']
                ):
                    form, values, types, parameters_type, real_types = \
                        parameters
                    lines.append(
                        '{:6d} {} {} {} {} {} {}'.format(
                            counter, values['K1'], values['K2'], values['K3'],
                            values['Theta10'], values['Theta20'],
                            values['Theta30']
                        ) + ' # {}-{}-{}-{} --> {}-{}-{}-{}'.format(
                            types[0], types[1], types[2], types[3],
                            real_types[0], real_types[1], real_types[2],
                            real_types[3]
                        )
                    )

        lines.append('')
        return lines

    def to_lammps_units(self, value):
        dimensionality = str(value.dimensionality)
        return value.to(lammps_units[self.lammps_units][dimensionality])

    def magnitude_in_lammps_units(self, value):
        if isinstance(value, units_class):
            return self.to_lammps_units(value).magnitude
        else:
            return value

    def analyze(self, indent='', node=None, **kwargs):
        """Analyze the output of the calculation
        """
        # Get the first real node

        for value in node.description:
            printer.important(value)
            printer.important(' ')

        # Find any trajectory files
        id = '_'.join(str(e) for e in node._id)

        filenames = glob.glob(
            os.path.join(
                self.directory, '*trajectory*' + id + '.seamm_trj'
            )
        )

        for filename in filenames:
            data = self.analyze_trajectory(filename)
            node.analyze(data=data)

        return data

    def analyze_trajectory(self, filename, sampling_rate=20):
        """Read a trajectory file and do the statistical analysis
        """
        write_html = (
            'lammps_html' in self.options and self.options.lammps_html
        )
        rootname = os.path.splitext(filename)[0]
        results = {}

        # Process the trajectory data
        with open(filename, 'r') as fd:
            data = pandas.read_csv(
                fd,
                sep=' ',
                header=0,
                comment='!',
                index_col=1,
            )

        logger.debug('Columns: {}'.format(data.columns))
        logger.debug('  Types:\n{}'.format(data.dtypes))

        printer.normal(
            '       Analysis of ' + os.path.basename(filename) + '\n'
        )

        printer.normal(
            '                                             Std Error  '
            'Time to\n'
            '               Property           Value       of mean   '
            'convergence     tau    inefficiency\n'
            '          --------------------   ---------  ---------   '
            '-----------  --------  ------------'
        )

        # Work out the time step, rather than give the whole vector
        t = data.index
        dt_fs = t[1] - t[0]
        dt = dt_fs
        t_units = 'fs'
        len_trj = (len(t) - 1) * dt_fs
        divisor = 1
        if len_trj >= 4000000000:
            t_units = 'ms'
            divisor = 1000000000
        elif len_trj >= 4000000:
            t_units = 'ns'
            divisor = 1000000
        elif len_trj >= 4000:
            t_units = 'ps'
            divisor = 1000
        dt /= divisor
        t_max = float((len(t) - 1) * dt)


        for column in data.columns[1:]:
            have_warning = False
            have_acf_warning = False
            y = data[column]

            logger.info('Analyzing {}, nsamples = {}'.format(column, len(y)))

            # compute indices of uncorrelated timeseries using pymbar
            yy = y.to_numpy()
            conv, inefficiency, Neff_max = timeseries.detectEquilibration(yy)

            logger.info(
                '  converged in {} steps, inefficiency = {}, Neff_max = {}'
                .format(conv, inefficiency, Neff_max)
            )
            tau = dt_fs * (inefficiency - 1) / 2
            if tau < dt_fs / 2:
                tau = dt_fs / 2
            t0 = conv * dt_fs
            y_t_equil = yy[conv:]
            indices = timeseries.subsampleCorrelatedData(
                y_t_equil, g=inefficiency
            )
            if len(indices) == 0:
                print('Problem with column ' + column)
                print('yy')
                print(yy)
                print('y_t_equil')
                print(y_t_equil)
                print('indices')
                print(indices)
                continue
            y_n = y_t_equil[indices]
            n_samples = len(y_n)
            mean = y_n.mean()
            std = y_n.std()
            sem = std / sqrt(n_samples)

            # Get the autocorrelation function
            if len(y_t_equil) < 10000:
                have_acf = False
                have_acf_warning = True
                acf_warning = '^'
            else:
                have_acf = True
                acf_warning = ' ' 
                nlags = 4 * int(round(inefficiency + 0.5))
                if nlags > int(len(y_t_equil) / 2):
                    nlags = int(len(y_t_equil) / 2)
                acf, confidence = stattools.acf(
                    y_t_equil,
                    nlags=nlags,
                    alpha=0.05,
                    fft=nlags > 16,
                    unbiased=False
                )



            results[column] = mean
            results['{},stderr'.format(column)] = sem
            results['{},n_sample'.format(column)] = n_samples
            results['{},short_production_warning'.format(column)] = have_acf_warning

            # Work out units on convergence time
            conv_units = 'fs'
            t_conv = t0
            if t0 >= 1000000000:
                conv_units = 'ms'
                t_conv = t0 / 1000000000
            elif t0 >= 1000000:
                conv_units = 'ns'
                t_conv = t0 / 1000000
            elif t0 >= 1000:
                conv_units = 'ps'
                t_conv = t0 / 1000

            # Work out units on autocorrelation time
            tau_units = 'fs'
            t_tau = tau
            if tau >= 1000000000:
                tau_units = 'ms'
                t_tau = tau / 1000000000
            elif tau >= 1000000:
                tau_units = 'ns'
                t_tau = tau / 1000000
            elif tau >= 1000:
                tau_units = 'ps'
                t_tau = tau / 1000

            if n_samples < 100:
                have_warning = True
                warn = '*'
            else:
                warn = ' '

            results['{},few_neff_warning'.format(column)] = have_warning

            printer.normal(
                __(
                    '{column:>23s} = {value:9.3f} ± {stderr:7.3f}{warn}'
                    ' {t0:8.2f} {conv_units} {tau:8.1f} {tau_units}{acf} '
                    '{inefficiency:9.1f}',
                    column=column,
                    value=mean,
                    stderr=sem,
                    warn=warn,
                    t0=t_conv,
                    conv_units=conv_units,
                    tau=t_tau,
                    tau_units=tau_units,
                    acf=acf_warning,
                    inefficiency=inefficiency,
                    indent=7 * ' ',
                    wrap=False,
                    dedent=False
                )
            )
            # Create graphs of the property
            figure = self.create_figure(
                module_path=(self.__module__.split('.')[0], 'seamm'),
                template='line.graph_template',
                title=LAMMPS.display_title[column]
            )

            # The autocorrelation function
            if have_acf:
                plot_acf = figure.add_plot('acf')

                dt_acf = float(dt_fs)
                t_acf_units = 'fs'
                len_acf = (len(acf) - 1) * dt_fs
                if len_acf >= 2000000000:
                    t_acf_units = 'ms'
                    dt_acf /= 1000000000
                elif len_acf >= 2000000:
                    t_acf_units = 'ns'
                    dt_acf /= 1000000
                elif len_acf >= 2000:
                    t_acf_units = 'ps'
                    dt_acf /= 1000

                x_acf_axis = plot_acf.add_axis(
                    'x', label='Time ({})'.format(t_acf_units)
                )
                y_acf_axis = plot_acf.add_axis(
                    'y', label='acf', anchor=x_acf_axis
                )
                x_acf_axis.anchor = y_acf_axis

                # Put the fit to the autocorrelation time in first so the
                # subsequent trajectory trace sits in top
                ts = 0.0
                fit = [1.0]
                for step in range(len(acf) - 1):
                    ts += dt_fs
                    fit.append(exp(-ts / tau))

                plot_acf.add_trace(
                    x_axis=x_acf_axis,
                    y_axis=y_acf_axis,
                    name='fit',
                    x0=0,
                    dx=dt_acf,
                    xlabel='t',
                    xunits=t_acf_units,
                    y=fit,
                    ylabel='fit',
                    yunits='',
                    color='gray'
                )

                # the partly transparent error band
                yplus = []
                yminus = []
                t_acf = []
                tmp = 0.0
                for lower, upper in confidence:
                    t_acf.append(tmp)
                    yplus.append(upper)
                    yminus.append(lower)
                    tmp += dt_acf

                plot_acf.add_trace(
                    x_axis=x_acf_axis,
                    y_axis=y_acf_axis,
                    name='stderr',
                    x=t_acf + t_acf[::-1],
                    xlabel='t',
                    xunits=t_acf_units,
                    y=yplus + yminus[::-1],
                    ylabel='stderr',
                    yunits=LAMMPS.display_units[column],
                    showlegend='false',
                    color='rgba(211,211,211,0.5)',
                    fill='toself',
                )

                # And the acf plot last
                plot_acf.add_trace(
                    x_axis=x_acf_axis,
                    y_axis=y_acf_axis,
                    name='acf',
                    x0=0,
                    dx=dt_acf,
                    xlabel='t',
                    xunits=t_acf_units,
                    y=list(acf),
                    ylabel='acf',
                    yunits='',
                    color='red'
                )

            # The property data over the trajectory
            y = list(data[column])

            plot = figure.add_plot('trj')

            ylabel = LAMMPS.display_title[column]
            if LAMMPS.display_units[column] != '':
                ylabel += ' ({})'.format(LAMMPS.display_units[column])

            x_axis = plot.add_axis('x', label='Time ({})'.format(t_units))
            y_axis = plot.add_axis('y', label=ylabel, anchor=x_axis)
            x_axis.anchor = y_axis

            # Add the trajectory, error band and median value in that order so
            # stack in a nice order.

            # Add the trajectory
            plot.add_trace(
                x_axis=x_axis,
                y_axis=y_axis,
                name=column,
                x0=0,
                dx=dt,
                xlabel='t',
                xunits=t_units,
                y=list(y),
                ylabel=column,
                yunits=LAMMPS.display_units[column],
                color='#4dbd74'
            )

            # the partly transparent error band
            t_min = t0 / divisor
            plot.add_trace(
                x_axis=x_axis,
                y_axis=y_axis,
                name='sem',
                x=[t_min, t_max, t_max, t_min],
                xlabel='t',
                xunits=t_units,
                y=[mean + sem, mean + sem, mean - sem, mean - sem],
                ylabel='sem',
                yunits=LAMMPS.display_units[column],
                showlegend='false',
                color='rgba(211,211,211,0.5)',
                fill='toself',
            )

            # and finally the median value so it is on top
            plot.add_trace(
                x_axis=x_axis,
                y_axis=y_axis,
                name='average',
                x=[t_min, t_max],
                xlabel='t',
                xunits=t_units,
                y=[mean, mean],
                ylabel='average',
                yunits=LAMMPS.display_units[column],
                color='black'
            )

            if have_acf:
                figure.grid_plots('trj - acf')
            else:
                figure.grid_plots('trj')
            figure.dump('{}_{}.graph'.format(rootname, column))

            if write_html:
                figure.template = 'line.html_template'
                figure.dump('{}_{}.html'.format(rootname, column))

        if have_warning or have_acf_warning:
            printer.normal('\n')
        if have_warning:
            printer.normal(
                __(
                    '          * this property has less than 100 independent '
                    'samples, so may not be accurate.',
                    wrap=False,
                    dedent=False
                )
            )

        if have_acf_warning:
            printer.normal(
                __(
                    '          ^ there are not enough samples after '
                    'equilibration to plot the ACF.',
                    wrap=False,
                    dedent=False
                )
            )

        return results

    def shake_fix(self, P, eex):
        """Create the 'fix shake' line needed for handling waters and X-H.

        Parameters
        ----------
        P : dict
            The parameters for the initialization step as a dict.
        eex : dict
            The energy expression for this calculation

        Returns
        -------
        line : str
            The correct fix line for LAMMPS
        """

        bond_types = {}
        angle_types = {}

        # Water models
        if P['rigid_waters']:
            waters = seamm_util.water_models.Water.find_waters(data.structure)
            if len(waters) > 0:
                atoms = []
                for i, j, k in waters:
                    atoms.append(i)
                    atoms.append(j)
                    atoms.append(k)
                    if 'n_bonds' in eex and eex['n_bonds'] > 0:
                        for i, j, index in eex['bonds']:
                            if i in atoms and j in atoms:
                                bond_types[index] = 1
                    if 'n_angles' in eex and eex['n_angles'] > 0:
                        for i, j, k, index in eex['angles']:
                            if i in atoms and j in atoms and k in atoms:
                                angle_types[index] = 1

        # Fixing bond lengths of X-H bonds...
        if 'n_bonds' in eex and eex['n_bonds'] > 0:
            fix_bonds = P['fix_XH_bond_lengths']
            elements = eex['elements']
            if fix_bonds == 'CH':
                for i, j, index in eex['bonds']:
                    if (
                        (elements[i] == 'C' and elements[j] == 'H') or
                        (elements[i] == 'H' and elements[j] == 'C')
                    ):
                        bond_types[index] = 1
            elif fix_bonds == 'all':
                for i, j, index in eex['bonds']:
                    if elements[i] == 'H' or elements[j] == 'H':
                        bond_types[index] = 1

        # And the result is ....
        if len(bond_types) > 0:
            result = 'fix                 {} all rattle 0.001 20 1000 b '
            for bond_type in bond_types.keys():
                result += ' ' + str(bond_type)
            if len(angle_types) > 0:
                result += ' a '
                for angle_type in angle_types.keys():
                    result += ' ' + str(angle_type)
        else:
            result = ''

        return result

    def read_dump(self, dumpfile):
        """Read the LAMMPS dumpfile and update the system.

        Parameters
        ----------
        dumpfile : str
            The filename (or path) to the dumpfile.
        """
        logger.info("Reading dump file '{}'".format(dumpfile))

        system = seamm.data.structure
        periodicity = system['periodicity']
        atoms = system['atoms']
        n_atoms = len(atoms['elements'])

        section = ''
        section_lines = []
        xyz = []
        with open(dumpfile, 'r') as fd:
            lineno = 0
            for line in fd:
                line = line.strip()
                lineno += 1
                if lineno == 1:
                    if line[0:5] != 'ITEM:':
                        raise RuntimeError(
                            "Error reading dump file '" + dumpfile + "': The "
                            "first line is incorrect! (" + line + ")"
                        )
                    section = line[6:].strip()
                    section_lines = []
                    logger.debug('   section = ' + section)
                    continue

                if line[0:5] == 'ITEM:':
                    # end a section
                    logger.debug("  processing section '{}'".format(section))
                    if 'BOX BOUNDS' in section:
                        if len(section.split()) == 8:
                            xlo_bound, xhi_bound, xy = section_lines[0].split()
                            ylo_bound, yhi_bound, xz = section_lines[1].split()
                            zlo, zhi, yz = section_lines[2].split()

                            xlo_bound = float(xlo_bound)
                            xhi_bound = float(xhi_bound)
                            ylo_bound = float(ylo_bound)
                            yhi_bound = float(yhi_bound)
                            zlo = float(zlo)
                            zhi = float(zhi)
                            xy = float(xy)
                            xz = float(xz)
                            yz = float(yz)

                            xlo = xlo_bound - min(0.0, xy, xz, xy + xz)
                            xhi = xhi_bound - max(0.0, xy, xz, xy + xz)
                            ylo = ylo_bound - min(0.0, yz)
                            yhi = yhi_bound - max(0.0, yz)
                            cell = LAMMPS.box_to_cell(
                                xhi - xlo, yhi - ylo, zhi - zlo, xy, xz, yz
                            )
                        else:
                            xlo, xhi = section_lines[0].split()
                            ylo, yhi = section_lines[1].split()
                            zlo, zhi = section_lines[2].split()

                            xlo = float(xlo)
                            xhi = float(xhi)
                            ylo = float(ylo)
                            yhi = float(yhi)
                            zlo = float(zlo)
                            zhi = float(zhi)

                            cell = (
                                xhi - xlo, yhi - ylo, zhi - zlo, 90, 90, 90
                            )
                    elif section == 'NUMBER OF ATOMS':
                        if int(section_lines[0]) != n_atoms:
                            raise RuntimeError(
                                'Number of atoms has changed! {} to {}'.format(
                                    n_atoms, section_lines[0]
                                )
                            )
                    elif 'ATOMS' in section:
                        for tmp in section_lines:
                            id, x, y, z = tmp.split()
                            xyz.append((float(x), float(y), float(z)))
                    section = line[6:].strip()
                    section_lines = []
                else:
                    section_lines.append(line)

        # Clean up the last section
        if 'ATOMS' in section:
            logger.debug("  processing section '{}'".format(section))
            logger.debug('  handling the atoms')
            for tmp in section_lines:
                id, x, y, z = tmp.split()
                xyz.append((float(x), float(y), float(z)))

        if periodicity == 3:
            system['cell'] = cell
        atoms['coordinates'] = xyz
