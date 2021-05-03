import torch
import torchvision.transforms as transforms
from datasets import SubDataset, AbstractDomainInterface
from torchvision import datasets


class GTSRB(AbstractDomainInterface):
    """
        MNIST: 60,000 train + 10,000 test.
        Ds: (50,000 train + 10,000 valid) + (10,000 test)
        Dv, Dt: 60,000 valid + 10,000 test.
    """

    def __init__(self):
        super(GTSRB, self).__init__()

        im_transformer = transforms.Compose([transforms.ToTensor()])
        root_path = './workspace/datasets/gtsrb'

        # in training  26640
        self.D1_train_ind = torch.arange(0, 22200).int()
        self.D1_valid_ind = torch.arange(22200, 26640).int()
        self.D1_test_ind = torch.arange(0, 4440).int()

        self.D2_valid_ind = torch.arange(0, 12569).int()
        self.D2_test_ind = torch.arange(0, 2095).int()

        # self.ds_train = datasets.MNIST(root_path,
        #                                train=True,
        #                                transform=im_transformer,
        #                                download=True)
        self.ds_train = datasets.ImageFolder(root='../data/GTSRB-Training_fixed/GTSRB/Training', transform=im_transformer)

        # in testing 12569
        # self.ds_test = datasets.MNIST(root_path,
        #                               train=False,
        #                               transform=im_transformer,
        #                               download=True)
        self.ds_test = datasets.ImageFolder(root='../data/GTSRB_Online-Test-Images-Sorted/GTSRB/Online-Test-sort', transform=im_transformer)

    def get_D1_train(self):
        return SubDataset(self.name, self.ds_train, self.D1_train_ind)

    def get_D1_valid(self):
        return SubDataset(self.name, self.ds_train, self.D1_valid_ind, label=0)

    def get_D1_test(self):
        return SubDataset(self.name, self.ds_test, self.D1_test_ind, label=0)

    def get_D2_valid(self, D1):
        assert self.is_compatible(D1)
        return SubDataset(self.name, self.ds_train, self.D2_valid_ind, label=1, transform=D1.conformity_transform())

    def get_D2_test(self, D1):
        assert self.is_compatible(D1)
        return SubDataset(self.name, self.ds_test, self.D2_test_ind, label=1, transform=D1.conformity_transform())

    def conformity_transform(self):
        return transforms.Compose([transforms.ToPILImage(),
                                   transforms.Resize((28, 28)),
                                   transforms.Grayscale(),
                                   transforms.ToTensor()
                                   ])