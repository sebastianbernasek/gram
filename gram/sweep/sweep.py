from os.path import join, abspath, relpath, isdir
from os import mkdir, chmod, pardir
import shutil
import pickle
import numpy as np
from time import time
from datetime import datetime

from .sampling import LogSampler
from ..simulation.environment import ConditionSimulation
from ..models.linear import LinearModel
from ..models.hill import HillModel
from ..models.twostate import TwoStateModel


class Sweep:
    """
    Class defines a single parameter sweep of a given model.

    Attributes:

        path (str) - path to sweep directory

        simulation_paths (dict) - paths to simulation directories

        base (np.ndarray[float]) - base parameter values

        delta (float or np.ndarray[float]) - log-deviations about base

        sampler (LogSampler) - sobol sample generator

        parameters (np.ndarray[float]) - sampled parameter values

        simulation_kwargs (dict) - keyword arguments for simulation

    """

    def __init__(self, base, delta=0.5):
        """
        Instantiate parameter sweep.

        Args:

            base (np.ndarray[float]) - base parameter values

            delta (float or np.ndarray[float]) - log-deviations about base

        """

        self.base = base
        self.delta = delta
        self.sampler = LogSampler(base-delta, base+delta)

    @property
    def N(self):
        """ Number of samples. """
        return self.samples.shape[0]

    @staticmethod
    def load(path):
        """ Load sweep from target <path>. """
        with open(join(path, 'sweep.pkl'), 'rb') as file:
            sweep = pickle.load(file)
        return sweep

    def initialize(self, dirpath='./'):
        """
        Create directory for sweep.

        Args:

            dirpath (str) - destination path

        """

        # assign name to sweep
        timestamp = datetime.fromtimestamp(time()).strftime('%y%m%d_%H%M%S')
        name = '{:s}_{:s}'.format(self.__class__.__name__, timestamp)

        # create directory (overwrite existing one)
        path = join(dirpath, name)
        if not isdir(path):
            mkdir(path)

        # make subdirectories for simulations and scripts
        self.path = path
        self.scripts_path = join(path, 'scripts')
        self.simulations_path = join(path, 'simulations')
        mkdir(self.scripts_path)
        mkdir(self.simulations_path)

    def build(self, dirpath='./', N=10, **kwargs):
        """
        Build and save simulation instance for each parameter sample.

        Args:

            dirpath (str) - destination path

            N (int) - number of samples

            kwargs: keyword arguments for ConditionSimulation

        """

        # create sweep directory
        self.initialize(dirpath)

        # store parameters
        self.parameters = self.sampler.sample(N)
        self.simulation_kwargs = kwargs
        self.simulation_paths = {}

        # build simulations
        for i, parameters in enumerate(self.parameters):
            simulation_path = join(self.path, 'simulations', '{:d}'.format(i))
            self.simulation_paths[i] = simulation_path
            self.build_simulation(parameters, simulation_path, **kwargs)

        # save serialized sweep
        with open(join(self.path, 'sweep.pkl'), 'wb') as file:
            pickle.dump(self, file, protocol=-1)

        # build parameter file and submission script
        self.write_paths_file()
        self.build_submission_script()

    @staticmethod
    def build_model(parameters):
        """
        Returns a model instance defined by the provided parameters.

        Args:

            parameters (np.ndarray[float]) - model parameters

        Returns:

            model (Cell instance)

        """
        pass

    @classmethod
    def build_simulation(cls, parameters, simulation_path, **kwargs):
        """
        Build and save a simulation instance for a specified set of parameters.

        Args:

            parameters (np.ndarray[float]) - parameter values

            simulation_path (str) - simulation path

            kwargs: keyword arguments for ConditionSimulation

        """

        # build model
        model = cls.build_model(parameters)

        # instantiate simulation
        simulation = ConditionSimulation(model, **kwargs)

        # create simulation directory
        if not isdir(simulation_path):
            mkdir(simulation_path)

        # save simulation
        simulation.save(simulation_path)

    def write_paths_file(self):
        """ Writes file containing all simulation paths. """
        paths = open(join(self.scripts_path, 'paths.txt'), 'w')
        for path in sweep.simulation_paths.values():
            paths.write('{:s} \n'.format(path))
        paths.close()

    def build_submission_script(self):
        """
        Write submission script for submitting multiple parallel simulations.
        """

        # define paths
        sweep_path = abspath(self.path)
        job_path = join(sweep_path, 'scripts', 'job_submission.sh')

        # copy run script to scripts directory
        run_path = join(abspath(__file__).rsplit('/', maxsplit=1)[0], 'run.py')
        shutil.copy(run_path, join(self.path, 'scripts'))

        # declare outer script that reads PATH from file
        job_script = open(job_path, 'w')
        job_script.write('#!/bin/bash\n')
        job_script.write('while IFS=$\'\\t\' read PATH\n')
        job_script.write('do\n')
        job_script.write('\tJOB=`msub - << EOJ\n\n')

        # =========== begin submission script for individual job =============
        job_script.write('#! /bin/bash\n')
        job_script.write('#MSUB -A p30653 \n')
        job_script.write('#MSUB -q short \n')
        job_script.write('#MSUB -l walltime=04:00:00 \n')
        job_script.write('#MSUB -m abe \n')
        job_script.write('#MSUB -M sebastian@u.northwestern.edu \n')
        job_script.write('#MSUB -j oe \n')
        #job_script.write('#MSUB -N %s \n' % job_id)
        job_script.write('#MSUB -l nodes=1:ppn=2 \n')
        job_script.write('#MSUB -l mem=1gb \n\n')

        # load python module
        job_script.write('module load python/anaconda3.6\n\n')

        # set working directory and run script
        job_script.write('cd {:s} \n\n'.format(sweep_path))

        # run script
        job_script.write('python scripts/run.py $\{PATH}\n')
        job_script.write('EOJ\n')
        job_script.write('`\n\n')
        # ============= end submission script for individual job =============

        # print job id
        job_script.write('echo "JobID = $\{JOB} submitted on `date`"\n')
        job_script.write('done < /scripts/paths.txt \n')
        job_script.write('exit\n')

        # close the file
        job_script.close()

        # change the permissions
        chmod(job_path, 0o755)


class LinearSweep(Sweep):

    """
    Parameter sweep for linear model. Parameters are:

        0: activation rate constant
        1: transcription rate constant
        2: translation rate constant
        3: deactivation rate constant
        4: mrna degradation rate constant
        5: protein degradation rate constant
        6: transcriptional feedback strength
        7: post-transcriptional feedback strength
        8: post-translational feedback strength

    """

    def __init__(self, base=None, delta=0.5):
        """
        Instantiate parameter sweep.

        Args:

            base (np.ndarray[float]) - base parameter values

            delta (float or np.ndarray[float]) - log-deviations about base

        """

        # define parameter ranges, log10(val)
        if base is None:
            base = np.array([0, 0, 0, 0, -2, -3, -4.5, -4.5, -4.5])

        # call parent instantiation
        super().__init__(base, delta)

    @staticmethod
    def build_model(parameters):
        """
        Returns a model instance defined by the provided parameters.

        Args:

            parameters (np.ndarray[float]) - model parameters

        Returns:

            model (LinearModel)

        """

        # extract parameters
        k0, k1, k2, g0, g1, g2, eta0, eta1, eta2 = parameters

        # instantiate base model
        model = LinearModel(k0=k0, k1=k1, k2=k2, g0=g0, g1=g1, g2=g2)

        # add feedback (two equivalent sets)
        model.add_feedback(eta0, eta1, eta2, perturbed=False)
        model.add_feedback(eta0, eta1, eta2, perturbed=True)

        return model


class HillSweep(Sweep):

    """
    Parameter sweep for hill model. Parameters are:

        0: transcription hill coefficient
        1: transcription rate constant
        2: translation rate constant
        3: mrna degradation rate constant
        4: protein degradation rate constant
        5: repressor michaelis constant
        6: repressor hill coefficient
        7: post-transcriptional feedback strength
        8: post-translational feedback strength

    """

    def __init__(self, base=None, delta=0.5):
        """
        Instantiate parameter sweep.

        Args:

            base (np.ndarray[float]) - base parameter values

            delta (float or np.ndarray[float]) - log-deviations about base

        """

        # define parameter ranges, log10(val)
        if base is None:
            base = np.array([0, 0, 0, -2, -3, -4, 0, -5, -4])

        # call parent instantiation
        super().__init__(base, delta)

    @staticmethod
    def build_model(parameters):
        """
        Returns a model instance defined by the provided parameters.

        Args:

            parameters (np.ndarray[float]) - model parameters

        Returns:

            model (HillModel)

        """

        # extract parameters
        n, k1, k2, g1, g2, k_m, r_n, eta1, eta2 = parameters

        # instantiate base model
        model = HillModel(k1=k1, k_m=.5, n=n, k2=k2, g1=g1, g2=g2)

        # add feedback (two equivalent sets)
        model.add_feedback(k_m, r_n, eta1, eta2, perturbed=False)
        model.add_feedback(k_m, r_n, eta1, eta2, perturbed=True)

        return model

class TwoStateSweep(Sweep):

    """
    Parameter sweep for twostate model. Parameters are:

        0: activation rate constant
        1: transcription rate constant
        2: translation rate constant
        3: deactivation rate constant
        4: mrna degradation rate constant
        5: protein degradation rate constant
        6: transcriptional feedback strength
        7: post-transcriptional feedback strength
        8: post-translational feedback strength

    """

    def __init__(self, base=None, delta=0.5):
        """
        Instantiate parameter sweep.

        Args:

            base (np.ndarray[float]) - base parameter values

            delta (float or np.ndarray[float]) - log-deviations about base

        """

        # define parameter ranges, log10(val)
        if base is None:
            base = np.array([0, 0, 0, -1, -2, -3, -4, -4.5, -4])

        # call parent instantiation
        super().__init__(base, delta)

    @staticmethod
    def build_model(parameters):
        """
        Returns a model instance defined by the provided parameters.

        Args:

            parameters (np.ndarray[float]) - model parameters

        Returns:

            model (LinearModel)

        """

        # extract parameters
        k0, k1, k2, g0, g1, g2, eta0, eta1, eta2 = parameters

        # instantiate base model
        model = TwoStateModel(k0=k0, k1=k1, k2=k2, g0=g0, g1=g1, g2=g2)

        # add feedback (two equivalent sets)
        model.add_feedback(eta0, eta1, eta2, perturbed=False)
        model.add_feedback(eta0, eta1, eta2, perturbed=True)

        return model