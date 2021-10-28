import json
import os.path
from os import path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import torch
import tqdm
from termcolor import colored
from torch.utils.data.dataloader import DataLoader

import global_vars as Global
from datasets import MirroredDataset
from methods import AbstractMethodInterface
from methods.nap.monitor import Monitor, EuclideanMonitor
from utils.iterative_trainer import IterativeTrainerConfig
from utils.logger import Logger
from .utils import CIFAR100sparse2coarse, get_nap_params
from plot import draw_activations


class NeuronActivationPatterns(AbstractMethodInterface):
    def __init__(self, args):
        super(NeuronActivationPatterns, self).__init__()
        self.base_model = None
        self.H_class = None
        self.args = args
        self.monitor = None
        self.class_count = 0
        self.default_model = 0
        self.threshold = 0
        self.best_monitored_count = 0
        self.add_identifier = ""
        self.known_loader = None
        self.unknown_loader = None
        self.train_loader = None
        self.nap_params = None
        self.train_dataset_name = ""
        self.valid_dataset_name = ""
        self.test_dataset_name = ""
        self.train_dataset_length = 0
        self.model_name = ""
        self.nap_cfg = None
        self.nap_cfg_path = "nap_cfgs/full_nets.json"
        self.nap_device = "cuda"

    def propose_H(self, dataset, mirror=True):
        config = self.get_H_config(dataset, mirror)

        from models import get_ref_model_path
        h_path = get_ref_model_path(self.args, config.model.__class__.__name__, dataset.name)
        best_h_path = path.join(h_path, 'model.best.pth')

        # trainer = IterativeTrainer(config, self.args)

        if not path.isfile(best_h_path):
            raise NotImplementedError("Please use model_setup to pretrain the networks first!")
        else:
            print(colored('Loading H1 model from %s' % best_h_path, 'red'))
            config.model.load_state_dict(torch.load(best_h_path))

        self.base_model = config.model
        self.base_model.eval()
        self.class_count = self.base_model.output_size()[1].item()
        self.add_identifier = self.base_model.__class__.__name__
        self.train_dataset_name = dataset.name
        self.model_name = "VGG" if self.add_identifier.find("VGG") >= 0 else "Resnet"
        with open(self.nap_cfg_path) as cf:
            self.nap_cfg = json.load(cf)
        if hasattr(self.base_model, 'preferred_name'):
            self.add_identifier = self.base_model.preferred_name()

    def train_H(self, dataset):
        self.known_loader = DataLoader(dataset.datasets[0], batch_size=self.args.batch_size, shuffle=True,
                                       num_workers=self.args.workers,
                                       pin_memory=True)
        self.unknown_loader = DataLoader(dataset.datasets[1], batch_size=self.args.batch_size, shuffle=True,
                                         num_workers=self.args.workers,
                                         pin_memory=True)
        self.valid_dataset_name = dataset.datasets[1].name
        return 0
        self.nap_params = self.nap_cfg[self.model_name][self.train_dataset_name]
        return self._find_only_threshold()
        # # return self._find_best_layer_to_monitor()

    def test_H(self, dataset):
        self.test_dataset_name = dataset.datasets[1].name
        # loader = DataLoader(dataset, batch_size=self.args.batch_size, shuffle=False,
        #                     num_workers=self.args.workers, pin_memory=True)
        # self._generate_test_distances(loader)
        # return 0

        # dataset = DataLoader(dataset, batch_size=self.args.batch_size, shuffle=False,
        #                      num_workers=self.args.workers, pin_memory=True)
        #
        # correct = 0.0
        # total_count = 0
        # print(f"quantiles {self.nap_params}")
        # print(f"threshold {self.threshold}")
        #
        # with tqdm.tqdm(total=len(dataset)) as pbar:
        #     with torch.no_grad():
        #         for i, (image, label) in enumerate(dataset):
        #             pbar.update()
        #
        #             # Get and prepare data.
        #             input, target = image.to(self.args.device), label.to(self.args.device)
        #
        #             outputs, intermediate_values, _ = self.base_model.forward_nap(input, nap_params=self.nap_params)
        #             _, predicted = torch.max(outputs.data, 1)
        #             distance = self.monitor.compute_hamming_distance(intermediate_values,
        #                                                         predicted.cpu().detach().numpy(), omit=self.omit)
        #
        #             classification = np.where(distance <= self.threshold, 0, 1)
        #
        #             correct += (classification == label.numpy()).sum()
        #
        #             total_count += len(input)
        #             message = 'Accuracy %.4f' % (correct / total_count)
        #             pbar.set_description(message)
        #
        # test_average_acc = correct / total_count
        # print("Final Test average accuracy %s" % (colored('%.4f%%' % (test_average_acc * 100), 'red')))
        # return test_average_acc.item()
        data = []
        self.omit = False
        with torch.no_grad():
            for pool_type in ["avg"]:
                for layer in range(7, 48, 8):
                    for pool in range(1, 3):
                        for q in np.linspace(0.1, 0.9, num=4):
                            nap_params = {
                                str(layer): {
                                    "pool_type": pool_type,
                                    "pool_size": pool,
                                    "quantile": q
                                }
                            }
                            self._get_layers_shapes(nap_params)
                            self.monitor = Monitor(self.class_count, self.nap_device,
                                                   layers_shapes=self.monitored_layers_shapes)
                            self._add_class_patterns_to_monitor(self.train_loader, nap_params=nap_params)
                            df_known = self._process_dataset(self.known_loader, nap_params=nap_params)
                            df_unknown = self._process_dataset(self.unknown_loader,
                                                               nap_params=nap_params)
                            threshold, acc = self._find_threshold(df_known, df_unknown, integers=True)

                            loader = DataLoader(dataset, batch_size=self.args.batch_size, shuffle=False,
                                                num_workers=self.args.workers, pin_memory=True)

                            correct = 0.0
                            total_count = 0
                            print(f"quantiles {nap_params}")
                            print(f"threshold {threshold}")

                            with tqdm.tqdm(total=len(loader)) as pbar:

                                for i, (image, label) in enumerate(loader):
                                    pbar.update()

                                    # Get and prepare data.
                                    input, target = image.to(self.args.device), label.to(self.args.device)

                                    outputs, intermediate_values, _ = self.base_model.forward_nap(input,
                                                                                                  nap_params=nap_params)
                                    _, predicted = torch.max(outputs.data, 1)
                                    distance = self.monitor.compute_hamming_distance(intermediate_values,
                                                                                     predicted.cpu().detach().numpy(),
                                                                                     omit=self.omit)

                                    classification = np.where(distance <= threshold, 0, 1)

                                    correct += (classification == label.numpy()).sum()

                                    total_count += len(input)
                                    message = 'Accuracy %.4f' % (correct / total_count)
                                    pbar.set_description(message)
                                test_average_acc = correct / total_count
                                print("Final Test average accuracy %s" % (
                                    colored('%.4f%%' % (test_average_acc * 100), 'red')))
                                data.append((self.model_name, dataset.datasets[0].name, self.valid_dataset_name,
                                             dataset.datasets[1].name,
                                             layer, pool, pool_type, q, threshold, acc, test_average_acc))
            # for pool_type in ["avg"]:
            #     for layer in [13, 14]:
            #         for q in np.linspace(0.1, 0.9, num=4):
            #             nap_params = {
            #                 str(layer): {
            #                     "pool_type": pool_type,
            #                     "pool_size": 0,
            #                     "quantile": q
            #                 }
            #             }
            #             self._get_layers_shapes(nap_params)
            #             self.monitor = Monitor(self.class_count, self.nap_device,
            #                                    layers_shapes=self.monitored_layers_shapes)
            #             self._add_class_patterns_to_monitor(self.train_loader, nap_params=nap_params)
            #             df_known = self._process_dataset(self.known_loader, nap_params=nap_params)
            #             df_unknown = self._process_dataset(self.unknown_loader,
            #                                                nap_params=nap_params)
            #             threshold, acc = self._find_threshold(df_known, df_unknown, integers=True)
            #
            #             loader = DataLoader(dataset, batch_size=self.args.batch_size, shuffle=False,
            #                                 num_workers=self.args.workers, pin_memory=True)
            #
            #             correct = 0.0
            #             total_count = 0
            #             print(f"quantiles {nap_params}")
            #             print(f"threshold {threshold}")
            #
            #             with tqdm.tqdm(total=len(loader)) as pbar:
            #
            #                 for i, (image, label) in enumerate(loader):
            #                     pbar.update()
            #
            #                     # Get and prepare data.
            #                     input, target = image.to(self.args.device), label.to(self.args.device)
            #
            #                     outputs, intermediate_values, _ = self.base_model.forward_nap(input,
            #                                                                                   nap_params=nap_params)
            #                     _, predicted = torch.max(outputs.data, 1)
            #                     distance = self.monitor.compute_hamming_distance(intermediate_values,
            #                                                                      predicted.cpu().detach().numpy(),
            #                                                                      omit=self.omit)
            #
            #                     classification = np.where(distance <= threshold, 0, 1)
            #
            #                     correct += (classification == label.numpy()).sum()
            #
            #                     total_count += len(input)
            #                     message = 'Accuracy %.4f' % (correct / total_count)
            #                     pbar.set_description(message)
            #
            #                 test_average_acc = correct / total_count
            #                 print("Final Test average accuracy %s" % (
            #                     colored('%.4f%%' % (test_average_acc * 100), 'red')))
            #                 data.append((self.model_name, dataset.datasets[0].name, self.valid_dataset_name,
            #                              dataset.datasets[1].name, layer,
            #                              0, pool_type, q, threshold, acc, test_average_acc))
        df = pd.DataFrame(data,
                          columns=['model', 'ds', 'dv', 'dt', 'layer', 'pool', 'pool_type', 'quantile', 'threshold',
                                   'valid_acc', 'test_acc'])
        fname = self.model_name + dataset.datasets[0].name + self.valid_dataset_name + dataset.datasets[1].name + "otherlayers.csv"
        fpath = os.path.join("results/article_plots", fname)
        df.to_csv(fpath)
        return test_average_acc.item()

    def method_identifier(self):
        output = "NeuronActivationPatterns"
        if len(self.add_identifier) > 0:
            output = output + "/" + self.add_identifier
        return output

    def get_H_config(self, dataset, mirror):
        if self.args.D1 in Global.mirror_augment and mirror:
            print(colored("Mirror augmenting %s" % self.args.D1, 'green'))
            new_train_ds = dataset + MirroredDataset(dataset)
            dataset = new_train_ds

        self.train_loader = DataLoader(dataset, batch_size=self.args.batch_size, num_workers=self.args.workers,
                                       pin_memory=True)
        self.train_dataset_length = len(dataset)
        # Set up the model
        model = Global.get_ref_classifier(self.args.D1)[self.default_model]().to(self.args.device)

        # Set up the config
        config = IterativeTrainerConfig()

        base_model_name = self.base_model.__class__.__name__
        if hasattr(self.base_model, 'preferred_name'):
            base_model_name = self.base_model.preferred_name()

        config.name = '_%s[%s](%s->%s)' % (self.__class__.__name__, base_model_name, self.args.D1, self.args.D2)
        config.train_loader = self.train_loader
        config.visualize = not self.args.no_visualize
        config.model = model
        config.logger = Logger()
        return config

    def _find_only_threshold(self):
        with torch.no_grad():
            last_layer_fraction = self.nap_params.pop("last_layer_fraction", None)
            self._get_layers_shapes(self.nap_params)
            self.monitor = Monitor(self.class_count, self.nap_device,
                                   layers_shapes=self.monitored_layers_shapes)
            if not last_layer_fraction:
                self.omit = False
            else:
                self.omit = True
                neurons_to_monitor = self._choose_neurons_to_monitor(
                    int(self.monitored_layers_shapes[0] * last_layer_fraction))
                self.monitor.set_neurons_to_monitor(neurons_to_monitor)

            # self._add_class_patterns_to_monitor(self.train_loader, nap_params=self.nap_params)
            # self._check_duplicates_count()
            self._draw_train_vs_valid_heatmaps(self.known_loader, self.unknown_loader, self.nap_params)
            return 0
            df_known = self._process_dataset(self.known_loader, nap_params=self.nap_params)
            df_unknown = self._process_dataset(self.unknown_loader, nap_params=self.nap_params)
            self.threshold, acc = self._find_threshold(df_known, df_unknown, integers=True)
            print(f"threshold: {self.threshold}, accuracy: {acc}")
            return acc

    def _find_best_layer_to_monitor(self):
        best_acc = 0
        results = []
        self.omit = False
        with torch.no_grad():
            for layer in range(0, 13, 2):
                for pool in range(1, 4):
                    # for q0 in np.concatenate((np.linspace(0.3, 0.5, num=3), np.linspace(0.81, 0.99, num=7))):
                    for q0 in np.linspace(0.1, 0.9, num=5):
                        for q1 in np.linspace(0.2, 0.2, num=1):
                            for q2 in np.linspace(0.2, 0.3, num=1):
                                for q3 in np.linspace(0.95, 0.97, num=1):
                                    for q4 in np.linspace(0.85, 0.9, num=1):
                                        # nap_params = [layer, pool, q0, q1, q2]
                                        # resnet_nap_params = [q0, q1, q2, q3, q4]
                                        nap_params = [layer, pool, q0]
                                        self._get_layers_shapes(nap_params)
                                        self.monitor = Monitor(self.class_count, self.nap_device,
                                                               layers_shapes=self.monitored_layers_shapes)
                                        self._add_class_patterns_to_monitor(self.train_loader, nap_params=nap_params)
                                        for i in tqdm.tqdm(np.linspace(int(self.monitored_layers_shapes[0]),
                                                                       self.monitored_layers_shapes[0] -
                                                                       self.monitored_layers_shapes[0] / 4, num=1)):

                                            # print(
                                            #     f" quantile0: {q0} quantile1: {q1} quantile2: {q2} quantile3: {q3} quantile4: {q4} lastlayer: {i}")
                                            print(f" quantile0: {q0} layer: {layer} pool: {pool} lastlayer: {i}")
                                            i = int(i)
                                            if self.omit:
                                                neurons_to_monitor = self._choose_neurons_to_monitor(i)
                                                self.monitor.set_neurons_to_monitor(neurons_to_monitor)
                                            df_known = self._process_dataset(self.known_loader, nap_params=nap_params)
                                            df_unknown = self._process_dataset(self.unknown_loader,
                                                                               nap_params=nap_params)
                                            threshold, acc = self._find_threshold(df_known, df_unknown, integers=True)
                                            results.append([q0, q1, q2, q3, q4, i, threshold, acc])
                                            if acc > best_acc + 0.01:
                                                self.threshold = threshold
                                                best_acc = acc
                                                self.best_monitored_count = i
                                                self.nap_params = nap_params
            for layer in [13, 14]:
                for q0 in np.linspace(0.1, 0.9, num=5):
                    for q1 in np.linspace(0.2, 0.2, num=1):
                        for q2 in np.linspace(0.2, 0.3, num=1):
                            for q3 in np.linspace(0.95, 0.97, num=1):
                                for q4 in np.linspace(0.85, 0.9, num=1):
                                    pool = 0
                                    nap_params = [layer, pool, q0, q1, q2]
                                    # nap_params = [q0, q1, q2, q3, q4]
                                    self._get_layers_shapes(nap_params)
                                    self.monitor = Monitor(self.class_count, self.nap_device,
                                                           layers_shapes=self.monitored_layers_shapes)
                                    self._add_class_patterns_to_monitor(self.train_loader, nap_params=nap_params)
                                    for i in tqdm.tqdm(np.linspace(int(self.monitored_layers_shapes[0]),
                                                                   self.monitored_layers_shapes[0] -
                                                                   self.monitored_layers_shapes[0] / 4, num=1)):

                                        # print(
                                        #     f" quantile0: {q0} quantile1: {q1} quantile2: {q2} quantile3: {q3} quantile4: {q4} lastlayer: {i}")
                                        print(f" quantile0: {q0} layer: {layer} pool: {pool} lastlayer: {i}")
                                        i = int(i)
                                        if self.omit:
                                            neurons_to_monitor = self._choose_neurons_to_monitor(i)
                                            self.monitor.set_neurons_to_monitor(neurons_to_monitor)
                                        df_known = self._process_dataset(self.known_loader, nap_params=nap_params)
                                        df_unknown = self._process_dataset(self.unknown_loader, nap_params=nap_params)
                                        threshold, acc = self._find_threshold(df_known, df_unknown, integers=True)
                                        results.append([q0, q1, q2, q3, q4, i, threshold, acc])
                                        if acc > best_acc + 0.01:
                                            self.threshold = threshold
                                            best_acc = acc
                                            self.best_monitored_count = i
                                            self.nap_params = nap_params
            for i in results:
                print(i)
        return best_acc

    def _process_dataset(self, testloader, nap_params=None):
        hamming_distance = np.array([])
        testiter = iter(testloader)

        for imgs, label in testiter:
            label = label.to(self.args.device)
            imgs = imgs.to(self.args.device)
            outputs, intermediate_values, _ = self.base_model.forward_nap(imgs, nap_params=nap_params)
            _, predicted = torch.max(outputs.data, 1)
            correct_bitmap = (predicted == label)
            distance = self.monitor.compute_hamming_distance(intermediate_values,
                                                             predicted.cpu().detach().numpy(), omit=self.omit)
            stacked = np.stack((label.cpu().numpy(), distance, correct_bitmap.cpu().numpy()), axis=1)
            if hamming_distance.size:
                hamming_distance = np.concatenate((hamming_distance, stacked))
            else:
                hamming_distance = stacked

        return pd.DataFrame(hamming_distance, columns=['class', 'hamming_distance', 'correct'], dtype=np.int32)

    def _find_threshold(self, df_known, df_unknown, integers=True, steps=1000):
        min = df_unknown["hamming_distance"].min() if df_unknown["hamming_distance"].min() > df_known[
            "hamming_distance"].min() else \
            df_known["hamming_distance"].min()
        max = df_unknown["hamming_distance"].max() if df_unknown["hamming_distance"].max() > df_known[
            "hamming_distance"].max() else \
            df_known["hamming_distance"].max()
        best_correct_count = 0
        best_threshold = 0
        for i in range(min - 1, max + 1) if integers else np.linspace(min, max, num=steps):
            correct_count = 0
            correct_count += (df_unknown["hamming_distance"] > i).sum()
            correct_count += (df_known["hamming_distance"] <= i).sum()
            if best_correct_count < correct_count:
                best_correct_count = correct_count
                best_threshold = i
        print(f" best threshold: {best_threshold}")
        print(f" accuracy: {best_correct_count / (len(df_unknown.index) + len(df_known.index))}")
        return best_threshold, best_correct_count / (len(df_unknown.index) + len(df_known.index))

    def _get_layers_shapes(self, nap_params):
        trainiter = iter(self.train_loader)
        with torch.no_grad():
            self.monitored_layers_shapes = \
                self.base_model.forward_nap(trainiter.__next__()[0][0].unsqueeze(0).to(self.args.device),
                                            nap_params=nap_params)[2]

    def _count_classes(self, loader):
        dataiter = iter(loader)
        count_class = dict()
        for _, label in tqdm.tqdm(dataiter):
            for i in range(label.shape[0]):
                if count_class.get(label[i].item()):
                    count_class[label[i].item()] += 1
                else:
                    count_class[label[i].item()] = 1
        return count_class

    def _count_classes_valid(self, loader, nap_params):
        dataiter = iter(loader)
        count_class = dict()
        for imgs, _ in dataiter:
            imgs = imgs.to(self.args.device)
            outputs, intermediate_values, _ = self.base_model.forward_nap(imgs, nap_params=nap_params)
            _, predicted = torch.max(outputs.data, 1)
            for i in range(predicted.shape[0]):
                if count_class.get(predicted[i].item()):
                    count_class[predicted[i].item()] += 1
                else:
                    count_class[predicted[i].item()] = 1
        return count_class

    def _draw_train_vs_valid_heatmaps(self, loader, valid_loader=None, nap_params=None):
        count_class = self._count_classes_valid(loader, nap_params)
        count_class_valid = self._count_classes_valid(valid_loader, nap_params)
        minimum = min(list(count_class.values()) + list(count_class_valid.values()))
        for k in sorted(count_class_valid):
            print(f"class: {k} count: {count_class_valid[k]}")

        return
        heatmap = dict()
        count_class_ = dict()
        heatmap_valid = dict()
        count_class_valid = dict()
        for i in range(len(count_class)):
            heatmap[i] = torch.Tensor()
            count_class_[i] = 0
            heatmap_valid[i] = torch.Tensor()
            count_class_valid[i] = 0

        dataiter = iter(loader)
        for img, label in tqdm.tqdm(dataiter):
            img = img.to(self.args.device)
            outputs, intermediate_values, _ = self.base_model.forward_nap(img, nap_params=nap_params)
            _, predicted = torch.max(outputs.data, 1)
            mat_torch = torch.zeros(intermediate_values.shape, device=intermediate_values.device)
            neuron_on_off_pattern = intermediate_values.gt(mat_torch).type(torch.int).to(torch.device(self.nap_device))
            predicted = predicted.cpu().numpy()
            for i in range(neuron_on_off_pattern.shape[0]):
                # if count_class_[predicted[i]] < minimum:
                    if heatmap[predicted[i]].numel():
                        heatmap[predicted[i]] += neuron_on_off_pattern[i]
                    else:
                        heatmap[predicted[i]] = neuron_on_off_pattern[i]
                    # count_class_[predicted[i]] += 1

        dataiter = iter(valid_loader)
        for img, label in tqdm.tqdm(dataiter):
            img = img.to(self.args.device)
            outputs, intermediate_values, shapes = self.base_model.forward_nap(img, nap_params=nap_params)
            _, predicted = torch.max(outputs.data, 1)
            mat_torch = torch.zeros(intermediate_values.shape, device=intermediate_values.device)
            neuron_on_off_pattern = intermediate_values.gt(mat_torch).type(torch.int).to(torch.device(self.nap_device))
            predicted = predicted.cpu().numpy()
            for i in range(neuron_on_off_pattern.shape[0]):
                # if count_class_valid[predicted[i]] < minimum:
                    if heatmap_valid[predicted[i]].numel():
                        heatmap_valid[predicted[i]] += neuron_on_off_pattern[i]
                    else:
                        heatmap_valid[predicted[i]] = neuron_on_off_pattern[i]
                    # count_class_valid[predicted[i]] += 1
        import seaborn as sns
        shapes_np = np.array(shapes)
        keys = set(heatmap.keys())
        diffs_sum = torch.Tensor([])
        for k in keys:
            if heatmap_valid[k].numel():
                diff = heatmap[k] - heatmap_valid[k]
            else:
                diff = heatmap[k]
            # _ = sns.heatmap(diff.reshape((shapes_np.min(), int(shapes_np.sum() / shapes_np.min()))).cpu())
            # title = "VGG_MNIST_class" + str(k) + "_vs_" + self.valid_dataset_name
            # plt.show()
            # plt.savefig(os.path.join("results/article_plots/heatmaps", title))
            # plt.close()
            if diffs_sum.numel():
                diffs_sum += diff
            else:
                diffs_sum = diff
        _ = sns.heatmap(diffs_sum.reshape((shapes_np.min(), int(shapes_np.sum() / shapes_np.min()))).cpu())
        title = "VGG_MNIST" + "_vs_" + self.valid_dataset_name
        # plt.show()
        plt.savefig(os.path.join("results/article_plots/heatmaps", title))
        plt.close()

    def _draw_train_heatmaps(self, loader, nap_params=None):
        count_class = self._count_classes(loader)

        minimum = min(count_class.values())
        heatmap = dict()
        count_class_ = dict()
        for i in range(len(count_class)):
            heatmap[i] = torch.Tensor()
            count_class_[i] = 0

        dataiter = iter(loader)
        for img, label in tqdm.tqdm(dataiter):
            label = label.to(self.args.device)
            img = img.to(self.args.device)
            _, intermediate_values, shapes = self.base_model.forward_nap(img, nap_params=nap_params)
            mat_torch = torch.zeros(intermediate_values.shape, device=intermediate_values.device)

            neuron_on_off_pattern = intermediate_values.gt(mat_torch).type(torch.int).to(torch.device(self.nap_device))
            label_np = label.cpu().numpy()

            for i in range(neuron_on_off_pattern.shape[0]):
                if count_class_[label_np[i]] < minimum:
                    if heatmap[label_np[i]].numel():
                        heatmap[label_np[i]] += neuron_on_off_pattern[i]
                    else:
                        heatmap[label_np[i]] = neuron_on_off_pattern[i]
                    count_class_[label_np[i]] += 1

        # draw_activations(intermediate_values, shapes)
        import seaborn as sns
        shapes_np = np.array(shapes)
        keys = set(heatmap.keys())
        keys2 = set(heatmap.keys())
        for k in keys:
            print(count_class_[k])
            print(heatmap[k].max())
            print(heatmap[k].min())
            print(heatmap[k].float().mean())
            print(heatmap[k].sum())
            print(heatmap[k].numel())
            print(heatmap[k].shape)
            _ = sns.heatmap(heatmap[k].reshape((shapes_np.min(), int(shapes_np.sum() / shapes_np.min()))).cpu())
            title = "VGG_MNIST_class" + str(k)
            # plt.show()
            plt.savefig(os.path.join("results/article_plots/heatmaps", title))
            plt.close()
            keys2.pop()
            diffs_sum = torch.Tensor([])

            for k2 in keys:
                if k != k2:
                    diff = heatmap[k] - heatmap[k2]
                    if diffs_sum.numel():
                        diffs_sum += diff
                    else:
                        diffs_sum = diff
            _ = sns.heatmap(diff.reshape((shapes_np.min(), int(shapes_np.sum() / shapes_np.min()))).cpu())
            title = "VGG_MNIST_class" + str(k) + "_diffsum"
            # plt.show()
            plt.savefig(os.path.join("results/article_plots/heatmaps", title))
            plt.close()
            for k2 in keys2:
                diff = heatmap[k] - heatmap[k2]
                _ = sns.heatmap(diff.reshape((shapes_np.min(), int(shapes_np.sum() / shapes_np.min()))).cpu())
                title = "VGG_MNIST_class" + str(k) + "_minus_" + str(k2)
                # plt.show()
                plt.savefig(os.path.join("results/article_plots/heatmaps", title))
                plt.close()

    def _add_class_patterns_to_monitor(self, loader, nap_params=None):
        count_class = self._count_classes(loader)
        self.monitor.set_class_patterns_count(count_class)
        dataiter = iter(loader)

        for img, label in tqdm.tqdm(dataiter):
            label = label.to(self.args.device)
            img = img.to(self.args.device)
            _, intermediate_values, shapes = self.base_model.forward_nap(img, nap_params=nap_params)

            self.monitor.add_neuron_pattern(intermediate_values, label.cpu().numpy())
        self.monitor.cut_duplicates()

    def _choose_neurons_to_monitor(self, neurons_to_monitor_count: int):
        neurons_to_monitor = {}
        for klass in range(self.class_count):
            class_weights = None
            for name, param in self.base_model.named_parameters():
                if name == "model.classifier.6.weight" or name == "model.fc.weight":
                    class_weights = param.data[klass].cpu().numpy()

            abs_weights = np.absolute(class_weights)
            neurons_to_monitor[klass] = abs_weights.argsort()[::-1][:neurons_to_monitor_count]

        return neurons_to_monitor

    def _check_duplicates_count(self):
        concat = torch.Tensor([])
        for i in self.monitor.known_patterns_set:
            print(
                f"i: {i} classcount: {self.monitor.class_patterns_count[i]} len:{len(self.monitor.known_patterns_set[i])}")
            if concat.numel():
                concat = torch.cat((concat, self.monitor.known_patterns_tensor[i].cpu()))
            else:
                concat = self.monitor.known_patterns_tensor[i].cpu()
            unique = concat.unique(return_counts=True, dim=0)
            print(f" unique concat max {unique[1].max()} nunique {(unique[1] == 1).sum()}")
            for j in unique[0]:
                print(j)

    def _generate_train_distances(self):
        for i in self.monitor.known_patterns_set:
            class_distances = np.ndarray([])
            for j in self.monitor.known_patterns_tensor[i]:
                s = ((self.monitor.known_patterns_tensor[i] ^ j) & j).sum(dim=1)
                s = s[s > 0]
                if s.numel():
                    distance = s.min().cpu().numpy().reshape((1,))
                else:
                    distance = np.array([0])
                if class_distances.shape:
                    class_distances = np.concatenate((class_distances, distance))
                else:
                    class_distances = distance
            fname = "distances2_model" + self.model_name + "_dataset_" + self.train_dataset_name + "_class_" + str(
                i) + ".csv"
            df = pd.DataFrame(class_distances, columns=['hamming_distance'])
            df.to_csv(fname)
            g = df.groupby("hamming_distance")
            print(f" fname {fname}, group: {g.size()} ")
        # shape = self.monitor.known_patterns_tensor.cpu().numpy().shape
        # print(f" unique all {self.monitor.known_patterns_tensor.reshape((shape)).unique(return_counts=True, dim=0)}")

    def _generate_test_distances(self, loader):
        test_distances = np.array([])
        with tqdm.tqdm(total=len(loader)) as pbar:
            with torch.no_grad():
                for i, (image, label) in enumerate(loader):
                    pbar.update()

                    # Get and prepare data.
                    input, target = image.to(self.args.device), label.to(self.args.device)

                    outputs, intermediate_values, _ = self.base_model.forward_nap(input, nap_params=self.nap_params)
                    _, predicted = torch.max(outputs.data, 1)
                    distance = self.monitor.compute_hamming_distance(intermediate_values,
                                                                     predicted.cpu().detach().numpy(),
                                                                     omit=self.omit)
                    if test_distances.size:
                        test_distances = np.concatenate((test_distances, distance))
                    else:
                        test_distances = distance

        fname = "testdistances_model" + self.model_name + "_dataset_" + self.train_dataset_name + "_vs_" + self.test_dataset_name + ".csv"
        df = pd.DataFrame(test_distances, columns=['hamming_distance'])
        df.to_csv(fname)
